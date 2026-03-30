# agent-memory

Claude Code用プラグイン。セッションをまたいだ会話の記憶を自動で保存・検索する。

セッション終了時にトランスクリプトをSQLiteに自動保存し、FTS5（全文検索）とベクトル検索（ruri-v3-130m）のハイブリッドで過去の会話を検索できる。日本語に最適化。

## できること

- **自動保存** — セッション終了時にトランスクリプトをDBに自動取り込み
- **過去の会話を検索** — 「前に何を決めた？」「あのとき話した内容」をスキルで検索
- **セッション開始時のコンテキスト注入** — 直近セッションの要点を自動でセッションに注入
- **日本語対応** — FTS5 trigramトークナイザ + ruri-v3日本語埋め込みモデル
- **クロスプラットフォーム** — Windows（Git Bash）/ macOS対応

## 動作要件

- Claude Code >= 1.0.0
- Python >= 3.12
- Node.js >= 18
- uv（推奨）または pip

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

### 3. Stop hookを設定（必須）

Claude Codeのバグ（[#29767](https://github.com/anthropics/claude-code/issues/29767)）により、プラグインのStop hookは実行されない。settings.jsonへの手動追加が必要。

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

> ユーザー名に括弧やUnicode文字が含まれる場合は8.3短縮名を使用する。`cmd /c "dir /x C:\Users"` で確認できる。

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

> SessionStart hookはプラグイン同梱の`hooks.json`で定義済みのため、手動設定は不要。

### 4. セッションを起動

初回起動時に自動的にセットアップが実行される:

1. Python仮想環境の作成と依存パッケージのインストール
2. ruri-v3-130mモデルのダウンロード（約130MB）
3. 既存セッションの一括取り込み

## 使い方

### 過去の会話を検索する

Claude Code内で自然言語で検索できる:

```
> 前にAPIの設計について何を決めた？
> 前回のセッションで話した内容を思い出して
```

プロジェクトで絞り込む場合:

```
> project:vault-root で引き継ぎについて検索して
```

CLIから直接実行することもできる:

```bash
node ~/.claude/plugins/local/agent-memory/scripts/run-search.mjs "検索クエリ"
node ~/.claude/plugins/local/agent-memory/scripts/run-search.mjs "project:my-project 認証フロー"
```

### 手動でセッションを取り込む

Stop hookが動いていなかった期間のセッションを取り込む場合:

```bash
cd ~/.claude/plugins/local/agent-memory/scripts

# macOS
.venv/bin/python backfill_external.py

# Windows
.venv/Scripts/python.exe backfill_external.py
```

## 設定

`config/settings.toml` を作成してデフォルト値を上書きできる。

### プロジェクトマッピング

作業ディレクトリとプロジェクトIDを紐づけると、検索時に `project:ID` でフィルタリングできる:

```toml
[projects]
"C:/PM_Vault" = "vault-root"
"/Users/you/myproject" = "my-project"
```

セッション開始時のcwdが上記パスと最長前方一致で照合され、プロジェクトIDが自動的に割り当てられる。

### 主な設定項目

| 項目 | デフォルト | 説明 |
|------|-----------|------|
| `token_budget` | 2000 | セッション開始時に注入する最大推定トークン数 |
| `recency_count` | 3 | 注入時に考慮する直近セッション数 |
| `result_limit` | 20 | 検索結果の最大件数 |
| `half_life_days` | 30.0 | 鮮度スコア減衰の半減期（日） |

全設定項目は `config/settings.default.toml` を参照。

## トラブルシューティング

### 検索結果が0件

```bash
# 依存パッケージの健全性を確認
cd ~/.claude/plugins/local/agent-memory/scripts
node -e "import('./_run.mjs').then(m=>m.runPython('_health.py'))"
```

### Stop hookでキャプチャされない

1. `settings.json` にStop hookのエントリがあるか確認
2. `data/logs/capture.log` でエラーを確認
3. 手動バックフィルで取り込む（上記「手動でセッションを取り込む」参照）

### venvが壊れた場合

```bash
rm -rf ~/.claude/plugins/local/agent-memory/scripts/.venv
# Claude Codeのセッションを再起動すると自動再構築される
```

### 完全リセット

```bash
rm ~/.claude/plugins/local/agent-memory/data/memory.db
rm ~/.claude/plugins/local/agent-memory/data/backfill_complete
# セッション再起動でDBを再構築し、バックフィルが実行される
```

## 技術概要

検索はFTS5 trigram（3段階フォールバック: フレーズ → OR → LIKE）とベクトルKNN（ruri-v3-130m, 512次元）の結果をRRF(k=60)でマージし、関連度70% + 鮮度30%の2因子でリランキングする。ベクトル検索はモデル未ダウンロード時にも自動スキップされ、FTS5のみで動作する。

内部アーキテクチャの詳細は [ARCHITECTURE.md](ARCHITECTURE.md) を参照。

## 既知の制約

- **Stop hookの手動設定が必要** — Claude Code [#29767](https://github.com/anthropics/claude-code/issues/29767) によりプラグインのStop hookが実行されない
- **Windowsのコンソールフラッシュ** — プラグインhook実行時にウィンドウが一瞬表示される場合がある（[#15572](https://github.com/anthropics/claude-code/issues/15572)）。bashラッパーで軽減済み
- **CPU推論のみ** — ベクトル埋め込みはCPUで動作。GPU未対応
- **リアルタイム同期なし** — セッション中の会話は終了時にまとめて取り込まれる
