# agent-memory

[English](README.md) | **日本語**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows | macOS](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS-lightgrey.svg)]()
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-Plugin-blueviolet.svg)]()

Claude Code用プラグイン。セッションをまたいだ会話の記憶を自動で保存・検索する。

セッション終了時にトランスクリプトをSQLiteに自動保存し、FTS5（全文検索）とベクトル検索（ruri-v3-130m）のハイブリッドで過去の会話を検索できる。日本語に最適化。

---

## 目次

- [機能](#機能)
- [クイックスタート](#クイックスタート)
- [インストール](#インストール)
- [使い方](#使い方)
- [設定](#設定)
- [トラブルシューティング](#トラブルシューティング)
- [仕組み](#仕組み)
- [既知の制約](#既知の制約)
- [コントリビュート](#コントリビュート)
- [ライセンス](#ライセンス)

## 機能

- **自動保存** --- セッション終了時にトランスクリプトをDBに自動取り込み
- **ハイブリッド検索** --- FTS5 trigram + ベクトルKNN（ruri-v3-130m）をRRFで統合
- **コンテキスト注入** --- セッション開始時に直近セッションの要点を自動注入
- **日本語最適化** --- trigramトークナイザ + 日本語埋め込みモデル
- **クロスプラットフォーム** --- Windows（Git Bash）/ macOS対応

## クイックスタート

```bash
# 1. プラグインを配置
cp -r agent-memory ~/.claude/plugins/local/agent-memory

# 2. settings.json で有効化
# 追加: "enabledPlugins": { "agent-memory@local": true }

# 3. Stop hook を settings.json に追加（下記「インストール」参照）

# 4. Claude Code を再起動 --- セットアップが自動実行される
```

初回起動時に依存パッケージのインストール、埋め込みモデルのダウンロード（約130MB）、既存セッションの一括取り込みが行われる。

## インストール

### 1. プラグインを配置

```bash
cp -r agent-memory ~/.claude/plugins/local/agent-memory
```

### 2. プラグインを有効化

`~/.claude/settings.json` に追加:

```json
{
  "enabledPlugins": {
    "agent-memory@local": true
  }
}
```

### 3. Stop hook を設定（必須）

Claude Codeのバグ（[#29767](https://github.com/anthropics/claude-code/issues/29767)）により、プラグインのStop hookは実行されない。`settings.json` への手動追加が必要。

**Windows:**

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash '/c/Users/SHORTN~1/.claude/plugins/local/agent-memory/scripts/run-capture.sh'",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

> ユーザー名に括弧やUnicode文字が含まれる場合は8.3短縮名を使用する。
> `cmd /c "dir /x C:\Users"` で確認できる。

**macOS:**

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash ~/.claude/plugins/local/agent-memory/scripts/run-capture.sh",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

> SessionStart hookはプラグイン同梱の `hooks.json` で定義済みのため、手動設定は不要。

### 4. セッションを起動

初回起動時に以下が自動実行される:

1. Python仮想環境の作成と依存パッケージのインストール
2. ruri-v3-130mモデルのダウンロード（約130MB）
3. 既存セッションの一括取り込み

## 使い方

### 過去の会話を検索

Claude Code内で自然言語で検索できる:

```
> 前にAPIの設計について何を決めた？
> 前回のセッションで話した内容を思い出して
> project:my-app で認証フローを検索
```

CLIから直接実行することもできる:

```bash
node ~/.claude/plugins/local/agent-memory/scripts/run-search.mjs "検索クエリ"
node ~/.claude/plugins/local/agent-memory/scripts/run-search.mjs "project:my-project 認証フロー"
```

### 手動バックフィル

Stop hookが動いていなかった期間のセッションを取り込む:

```bash
cd ~/.claude/plugins/local/agent-memory/scripts

# macOS
.venv/bin/python backfill_external.py

# Windows
.venv/Scripts/python.exe backfill_external.py
```

## 設定

`config/settings.toml` を作成してデフォルト値を上書きできる。全設定項目は `config/settings.default.toml` を参照。

### プロジェクトマッピング

作業ディレクトリとプロジェクトIDを紐づけると、検索時に `project:ID` でフィルタリングできる:

```toml
[projects]
"/Users/you/repos/my-app" = "my-app"
"C:/Users/you/repos/another" = "another"
```

セッション開始時のcwdが上記パスと最長前方一致で照合され、プロジェクトIDが自動的に割り当てられる。

### 設定リファレンス

| 項目 | デフォルト | 説明 |
|------|-----------|------|
| `token_budget` | 2000 | セッション開始時に注入する最大推定トークン数 |
| `recency_count` | 3 | 注入時に考慮する直近セッション数 |
| `result_limit` | 20 | 検索結果の最大件数 |
| `half_life_days` | 30.0 | 鮮度スコア減衰の半減期（日） |
| `fts_candidate_limit` | 100 | FTS5/Vec検索の候補プールサイズ |
| `inject_cache_ttl_seconds` | 300 | 注入キャッシュの有効期間 |
| `recency_floor` | 0.01 | 鮮度スコアの下限値 |
| `access_boost_factor` | 0.3 | ヒット回数ブーストの重み |
| `vec.enabled` | true | ベクトル検索の有効/無効 |

## トラブルシューティング

### 検索結果が0件

```bash
cd ~/.claude/plugins/local/agent-memory/scripts
node -e "import('./_run.mjs').then(m=>m.runPython('_health.py'))"
```

### Stop hookでキャプチャされない

1. `settings.json` にStop hookのエントリがあるか確認
2. `data/logs/capture.log` でエラーを確認
3. 手動バックフィルで取り込む（上記「[使い方](#手動バックフィル)」参照）

### venvが壊れた場合

```bash
rm -rf ~/.claude/plugins/local/agent-memory/scripts/.venv
# Claude Codeのセッションを再起動すると自動再構築される
```

### 完全リセット

```bash
rm ~/.claude/plugins/local/agent-memory/data/memory.db
rm ~/.claude/plugins/local/agent-memory/data/backfill_complete
# Claude Code再起動でDBを再構築し、バックフィルが実行される
```

## 仕組み

```
セッション開始                セッション終了                 検索（スキル）
     |                              |                            |
 setup.sh（初回のみ）         run-capture.sh               run-search.mjs
     |                              |                            |
 setup.mjs                    capture.py                    search.py
 venv + モデル + バックフィル   JSONL差分パース               FTS5 + Vec
     |                        chunks -> SQLite               RRFマージ
 inject.py                    backfill_vec.py               2因子リランキング
 直近コンテキスト -> stdout    埋め込み生成（非同期）         hit_count更新
```

検索はFTS5 trigram（3段階フォールバック: フレーズ -> OR -> LIKE）とベクトルKNN（ruri-v3-130m, 512次元）をRRF(k=60)でマージし、関連度（70%）+ 鮮度（30%）の2因子でリランキングする。ベクトル検索はモデル未ダウンロード時にも自動スキップされ、FTS5のみで動作する。

内部アーキテクチャの詳細は [ARCHITECTURE.md](ARCHITECTURE.md) を参照。

## 既知の制約

| 制約 | 影響度 | 回避策 |
|------|--------|--------|
| Stop hookの手動設定が必要 | 中 | Claude Code [#29767](https://github.com/anthropics/claude-code/issues/29767) |
| Windowsのコンソールフラッシュ | 低 | bashラッパーで軽減済み（[#15572](https://github.com/anthropics/claude-code/issues/15572)） |
| CPU推論のみ | 中 | GPU未対応 |
| リアルタイム同期なし | 低 | セッション終了時にまとめて取り込み |

## 動作要件

- Claude Code >= 1.0.0
- Python >= 3.12
- Node.js >= 18
- [uv](https://docs.astral.sh/uv/)（推奨）または pip

## コントリビュート

プルリクエストを歓迎します。大きな変更の場合は、まずissueを開いて変更内容を議論してください。

### 開発環境セットアップ

```bash
git clone https://github.com/jsakuta/agent-memory.git
cd agent-memory/scripts
uv venv .venv
uv pip install -e .
```

## ライセンス

[MIT](LICENSE)
