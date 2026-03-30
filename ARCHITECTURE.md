# Architecture

agent-memoryプラグインの内部アーキテクチャ。開発・保守・デバッグ向け。

ユーザー向けのセットアップ・使い方は [README.md](README.md) を参照。

## データフロー

```
SessionStart hook                Stop hook                    mem-search スキル
       |                              |                              |
  setup.sh (初回のみ)           run-capture.sh                  run-search.mjs
       |                              |                              |
  setup.mjs                     run-capture.mjs                  search.py
  venv構築                      capture.py                       FTS5 + Vec検索
  backfill_all.py                 JSONL差分パース                RRFマージ
    (fire-and-forget)             chunks + FTS5に投入            2因子リランキング
       |                         backfill_vec.py                 hit_count更新
  run-inject.sh                   (fire-and-forget)
       |                           Vec埋め込み生成
  run-inject.mjs
       |
  inject.py
  DBから直近チャンクを取得
  recencyスコアで選別
  additionalContextとして出力
```

## Hook構成

### SessionStart（hooks.json — クロスプラットフォーム）

| matcher | スクリプト | timeout | 処理内容 |
|---------|-----------|---------|---------|
| `startup` | setup.sh → setup.mjs | 120秒 | venv構築、モデルDL、初回バックフィル |
| `startup\|resume` | run-inject.sh → inject.py | 5秒 | 直近セッションのコンテキスト注入 |

hooks.jsonでは `${CLAUDE_PLUGIN_ROOT}` を使用し、プラットフォームに依存しない。

### Stop（settings.json — OS固有）

| スクリプト | timeout | 処理内容 |
|-----------|---------|---------|
| run-capture.sh → run-capture.mjs → capture.py | 5秒 | JSONL差分キャプチャ + Vec backfill起動 |

プラグインhooks.jsonのStop hookはClaude Code Issue #29767により実行されないため、settings.jsonに直接記述する必要がある。パスはOS固有のためgit同期せず、各OSのsettings.jsonで個別管理する。

## Capture（JSONL → DB取り込み）

### 処理フロー

1. `_health.py` のlightチェック（health.json読み取りのみ）で連続失敗3回以上ならスキップ
2. stdinからhook入力を受け取る（`session_id`, `transcript_path`, `cwd`）
3. Windowsの場合、Git Bashパス（`/c/Users/...`）をWindowsパス（`C:/Users/...`）に変換
4. `processing_state`テーブルから前回のバイトオフセットを取得
5. ファイルサイズが前回オフセット以下なら終了（差分なし）
6. `_parser.py` のFSMパーサーでJSONLを解析し、Exchange（user + assistant）ペアを抽出
7. chunksテーブルとchunks_fts（FTS5）にINSERT
8. sessionsテーブルとprocessing_stateをUPSERT
9. time_limit（4.5秒）を超えた場合は途中で中断し、次回に続きを処理

### FSMパーサー（_parser.py）

```
State遷移:
  IDLE → IN_TURN     userメッセージ受信
  IN_TURN → IDLE     次のuserメッセージで前チャンク確定
  * → POST_COMPACT   compact_boundary受信

フィルタリング:
  - tool_resultのみのメッセージ → スキップ
  - systemメッセージ → スキップ
  - <task-notification>, <local-command-caveat> 等のタグ → 除去
```

各Exchangeに付帯するメタデータ:
- `timestamp`: 最初のメッセージのISO 8601タイムスタンプ
- `git_branch`: セッション中のブランチ名
- `files_touched`: 操作されたファイルパス（JSON配列）
- `tools_used`: 使用されたツール名（JSON配列）
- `api_tokens`: 出力トークン数
- `is_compact_summary`: コンテキスト圧縮後の要約フラグ

## Inject（コンテキスト注入）

### 処理フロー

1. health.jsonのlightチェック
2. 冪等性キャッシュ確認（`inject_cache/{session_id}.json`、TTL 300秒）
3. DBから直近セッション（`recency_count`件）を取得
   - projectが解決済み → projectでフィルタ
   - projectなし・cwdあり → cwdの前方一致でフィルタ
   - cwdもなし → 全セッションから取得
4. 各セッションから直近5チャンクを取得し、recencyスコア最高の1つを選出
5. トークン予算（`token_budget`）内でフォーマット
6. `hookSpecificOutput.additionalContext` としてstdoutにJSON出力

### Recencyスコア計算

```
days = (now - last_accessed) / 86400
decay = 0.5 ^ (days / half_life_days)
boost = 1.0 + access_boost_factor * log1p(hit_count)
recency = max(decay * boost, recency_floor)
```

デフォルト: half_life_days=30.0, recency_floor=0.01, access_boost_factor=0.3

### 制約

- onnxruntimeをimportしない（遅いため）
- timeout 5秒以内で完了する必要がある

## Search（ハイブリッド検索）

### FTS5検索（3段階フォールバック）

| Stage | 戦略 | 条件 |
|-------|------|------|
| 0 | フレーズ完全一致 | クエリ全体を`"`で囲んで検索 |
| 1 | OR検索 | 結果が5件未満の場合、3文字以上の各単語をORで検索 |
| 2 | LIKEフォールバック | FTS5で0件の場合、`LIKE %query%` で検索 |

BM25スコアリング: `user_text`に重み3.0、`assistant_text`に重み1.0。

### Vec検索

- クエリを「検索クエリ: 」接頭辞付きでruri-v3-130mに入力し、512次元ベクトルを生成
- sqlite-vecの`MATCH`でcosine距離による上位`fts_candidate_limit`件を取得
- embedderが利用不可の場合は自動スキップ

### RRFマージ

```
score = 1/(k + fts_rank) + 1/(k + vec_rank)    k=60
```

片方にしかないchunkは、欠損側のrankを `fts_candidate_limit + 1` として計算。

### 2因子リランキング

```
final = 0.7 * relevance_norm + 0.3 * recency_norm
```

relevanceとrecencyをそれぞれMin-Max正規化してから合算。ヒットしたchunkの`hit_count`と`last_accessed`を更新する（強化学習的フィードバック）。

## データベース

SQLite + WALモード。PRAGMA: `journal_mode=WAL`, `busy_timeout=3000`。

### スキーマ

**sessions**

| カラム | 型 | 説明 |
|--------|------|------|
| session_id | TEXT PK | セッションUUID |
| project | TEXT | プロジェクトID（settings.tomlのマッピングで解決） |
| started_at | TEXT | セッション開始日時（ISO 8601） |
| last_updated | TEXT | 最終更新日時 |
| message_count | INTEGER | メッセージ数 |
| cwd | TEXT | セッション時の作業ディレクトリ |

**chunks**

| カラム | 型 | 説明 |
|--------|------|------|
| id | INTEGER PK | 自動採番 |
| session_id | TEXT | セッションUUID |
| chunk_index | INTEGER | セッション内の通番 |
| user_text | TEXT | ユーザー入力 |
| assistant_text | TEXT | アシスタント応答 |
| timestamp | TEXT | ISO 8601 |
| hit_count | INTEGER | 検索ヒット回数 |
| last_accessed | TEXT | 最終アクセス日時 |
| git_branch | TEXT | Gitブランチ名 |
| files_touched | TEXT | JSON配列 |
| tools_used | TEXT | JSON配列 |
| char_count | INTEGER | テキスト総文字数 |
| api_tokens | INTEGER | 出力トークン数 |
| is_compact_summary | INTEGER | 圧縮要約フラグ |

**chunks_fts**（FTS5仮想テーブル）

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    user_tokenized, assistant_tokenized,
    content='', content_rowid='id', contentless_delete=1,
    tokenize='trigram'
);
```

trigramトークナイザにより日本語を含むあらゆる言語の部分文字列検索に対応。

**vec_chunks**（sqlite-vec仮想テーブル）

```sql
CREATE VIRTUAL TABLE vec_chunks USING vec0(
    embedding float[512]
);
```

rowidでchunksテーブルと紐付く。

**processing_state**

| カラム | 型 | 説明 |
|--------|------|------|
| session_id | TEXT PK | セッションUUID |
| last_processed_offset | INTEGER | JSONLファイルの処理済みバイト位置 |
| checksum | TEXT | ファイル整合性チェック用 |
| backfill_requested | INTEGER | Vec埋め込み再生成フラグ |

### スキーママイグレーション

`schema_version`テーブルでバージョンを管理。`_db.py`の`init_db()`で自動マイグレーション。

| バージョン | 変更内容 |
|-----------|---------|
| v1 | 初期スキーマ（fugashi分かち書き） |
| v2 | FTS5 trigramトークナイザに移行 |

## バックフィル

| スクリプト | 用途 | 起動タイミング |
|-----------|------|---------------|
| `backfill_all.py` | 下記2つを順に実行。完了フラグ（`data/backfill_complete`）がある場合スキップ | setup.mjsからfire-and-forget |
| `backfill_external.py` | `~/.claude/projects/`配下のJSONLを`capture.process_session()`で取り込み | backfill_all.pyまたは手動 |
| `backfill_projects.py` | sessionsのprojectカラムをcwdから再計算。`--all`で設定済みも再計算 | 手動 |
| `backfill_vec.py` | vec_chunksに埋め込み未生成のchunksを処理。PIDロックで多重起動防止 | run-capture.mjsからfire-and-forget、または手動 |

### backfill_vec.pyのロック機構

- `data/logs/backfill_vec.lock` にPIDとタイムスタンプを書き込み
- 既存ロックがある場合、PIDの生存確認 + 30分の鮮度チェック
- staleロックは自動削除して続行

## クロスプラットフォーム

### _run.mjs（Pythonランナー）

Node.jsからPythonスクリプトを呼び出すクロスプラットフォームラッパー。

venvの解決順序:
1. `$CLAUDE_PLUGIN_DATA/.venv/` — マーケットプレイスインストール時
2. `scripts/.venv/` — ローカル開発時
3. プラグインルート `/.venv/` — setup.mjsのフォールバック

各パスでプラットフォームに応じたバイナリ名を使用:
- Windows: `Scripts/python.exe`
- macOS/Linux: `bin/python`

### .shラッパー

`setup.sh`, `run-inject.sh`, `run-capture.sh` はbash経由でnode（.mjs）を起動する。Windowsでbashがnon-consoleの親プロセスとなり、子プロセス（node.exe/python.exe）のコンソールウィンドウ表示を抑制する。

### Windows固有の対応

| 対応 | ファイル | 内容 |
|------|---------|------|
| Git Bashパス変換 | capture.py | `/c/Users/...` → `C:/Users/...` |
| Python 3.13 WMIハング回避 | _common.py | `platform._uname_cache`を事前設定しWMIクエリをバイパス |
| 8.3短縮名 | settings.json | ユーザー名に括弧等がある場合に必要 |

## ファイル構成

```
agent-memory/
+-- .claude-plugin/
|   +-- plugin.json              # プラグイン定義（v1.0.0）
+-- config/
|   +-- settings.default.toml    # デフォルト設定（git管理）
|   +-- settings.toml            # ユーザー設定（git除外、パスマッピング含む）
+-- hooks/
|   +-- hooks.json               # SessionStart hookのみ
+-- skills/
|   +-- mem-search/
|       +-- SKILL.md             # 検索スキル定義
+-- scripts/
|   +-- setup.sh / setup.mjs     # 初回セットアップ
|   +-- run-inject.sh / .mjs     # SessionStart: コンテキスト注入
|   +-- run-capture.sh / .mjs    # Stop: JSONL→DB取り込み
|   +-- run-search.mjs           # 検索エンジン起動
|   +-- _run.mjs                 # クロスプラットフォームPythonランナー
|   +-- _common.py               # 共通ユーティリティ（パス解決、設定、recency計算）
|   +-- _db.py                   # SQLite接続、スキーマ初期化、マイグレーション
|   +-- _parser.py               # JSONL FSMパーサー
|   +-- _embedder.py             # ruri-v3-130m ONNX推論
|   +-- _health.py               # 健全性チェック（health.json読み取り + CLI診断）
|   +-- _path_resolver.py        # .claude/projects/パス解決（キャッシュ付き）
|   +-- inject.py                # SessionStart hookロジック
|   +-- capture.py               # Stop hookロジック
|   +-- search.py                # ハイブリッド検索エンジン
|   +-- backfill_all.py          # 全バックフィル（external + vec）
|   +-- backfill_external.py     # 外部JSONL一括取り込み
|   +-- backfill_projects.py     # プロジェクトマッピング再計算
|   +-- backfill_vec.py          # Vec埋め込み一括生成
|   +-- pyproject.toml           # Python依存関係
+-- models/
|   +-- ruri-v3-130m/            # ONNXモデル（git除外、自動ダウンロード）
|       +-- model_int8.onnx
|       +-- tokenizer.json
+-- data/                        # ランタイムデータ（git除外）
    +-- memory.db
    +-- inject_cache/
    +-- backfill_complete
    +-- logs/
```

## 設定の読み込み（_common.py）

1. `get_plugin_root()`: `__file__`の親の親（scriptsの親 = プラグインルート）
2. `get_data_root()`: `$CLAUDE_PLUGIN_DATA` があればそれ、なければプラグインルート
3. `load_config()`: `settings.default.toml` → `settings.toml` の順で上書きマージ
4. `get_db_path()`: `get_data_root() / config["db_path"]`

## 健全性チェック（_health.py）

`read_health_status()` が `data/logs/health.json` を読み取り、連続失敗回数を返す。
連続失敗3回以上の場合、inject.pyは警告メッセージを注入し、capture.pyはキャプチャをスキップする。

CLI診断（`python _health.py`）で依存パッケージ・DB整合性を確認可能。
