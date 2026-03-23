# agent-memory 外部配布対応 実装計画

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** agent-memory プラグインを Windows + Mac のB&DXチームメンバーに配布可能にする

**Architecture:** Node.js (.mjs) ラッパーが OS を判定し、`${CLAUDE_PLUGIN_DATA}` 内の venv の Python を呼び出す。設定は default（リポ同梱）+ user-local（PLUGIN_DATA）の2層構造。Python スクリプト本体は無改修。

**Tech Stack:** Node.js (hooks), Python 3.12+ (scripts), uv (venv管理), Git LFS (ONNXモデル)

**前提条件:** uv, Python 3.12+, Node.js がインストール済み

---

## 変更概要

| 変更対象 | Before | After |
|----------|--------|-------|
| hooks.json | `.venv/Scripts/python` 固定 | `node run-*.mjs` → OS判定 → PLUGIN_DATA の python |
| settings.toml | リポに個人パス入り | `settings.default.toml`（リポ）+ `settings.toml`（PLUGIN_DATA） |
| data/, logs/ | PLUGIN_ROOT 内 | PLUGIN_DATA 内 |
| .venv/ | PLUGIN_ROOT/scripts/ 内 | PLUGIN_DATA/.venv/ |
| models/ | .gitignore で除外中 | Git LFS でトラッキング |
| Python スクリプト | 変更なし | `_common.py` のパス解決のみ修正 |

---

## Task 1: `_common.py` のパス解決を PLUGIN_DATA 対応にする

**Files:**
- Modify: `scripts/_common.py`

**Step 1: `_common.py` の `get_data_root()` 関数を追加**

`CLAUDE_PLUGIN_DATA` 環境変数があればそちらを使い、なければ従来の `get_plugin_root() / "data"` にフォールバック:

```python
def get_data_root() -> Path:
    """Runtime data の root。PLUGIN_DATA > PLUGIN_ROOT/data の順で解決。"""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data)
    return get_plugin_root()
```

**Step 2: `get_db_path()` を修正**

```python
def get_db_path(config: dict | None = None) -> Path:
    if config is None:
        config = load_config()
    return get_data_root() / config["db_path"]
```

**Step 3: `load_config()` を2層読み込みに修正**

```python
def load_config() -> dict:
    # 1. デフォルト設定（PLUGIN_ROOT 同梱）
    default_path = get_plugin_root() / "config" / "settings.default.toml"
    with open(default_path, "rb") as f:
        config = tomllib.load(f)
    # 2. ユーザー設定（PLUGIN_DATA、あれば上書きマージ）
    user_path = get_data_root() / "settings.toml"
    if user_path.exists():
        with open(user_path, "rb") as f:
            user_config = tomllib.load(f)
        # shallow merge (user overrides default)
        config.update(user_config)
    return config
```

**Step 4: `get_logger()` のログパスを修正**

```python
def get_logger(name: str):
    import logging
    log_dir = get_data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    # ... 以降同じ
```

**Step 5: `import os` を追加**

ファイル冒頭の import に `os` を追加。

**Step 6: テスト — Python が _common.py を読み込めることを確認**

```bash
cd ~/.claude/plugins/local/agent-memory/scripts
.venv/Scripts/python -c "from _common import get_data_root, load_config; print(get_data_root()); print(load_config())"
```

Expected: CLAUDE_PLUGIN_DATA 未設定時は従来のパスが返る。

---

## Task 2: settings.toml → settings.default.toml にリネーム + ユーザー設定分離

**Files:**
- Rename: `config/settings.toml` → `config/settings.default.toml`
- Create: `config/settings.default.toml`（[projects] なし版）

**Step 1: 現在の settings.toml をバックアップ**

```bash
cp config/settings.toml config/settings.toml.bak
```

**Step 2: settings.default.toml を作成（個人パスなし）**

```toml
db_path = "memory.db"
token_budget = 2000
recency_count = 3
fts_candidate_limit = 100
result_limit = 20
inject_cache_ttl_seconds = 300
half_life_days = 14.0
recency_floor = 0.01
access_boost_factor = 0.3

# [projects] はユーザーローカル設定（${CLAUDE_PLUGIN_DATA}/settings.toml）に記載
# 例:
# [projects]
# "/Users/yourname/repos/my-project" = "my-project"

[vec]
enabled = true
model_path = "models/ruri-v3-30m"
embedding_dim = 256
```

**Step 3: 旧 settings.toml を削除し、ユーザーローカルに移動**

現在の [projects] セクション付き settings.toml は手動で `${CLAUDE_PLUGIN_DATA}/settings.toml` にコピー（既存環境の場合）。

**Step 4: テスト — load_config() が2層マージで動くことを確認**

```bash
CLAUDE_PLUGIN_DATA=/tmp/test-agent-memory .venv/Scripts/python -c "
from _common import load_config
# default のみ（PLUGIN_DATA に settings.toml がない場合）
c = load_config()
print('db_path:', c['db_path'])
print('projects:', c.get('projects', {}))
"
```

Expected: `db_path: memory.db`, `projects: {}`

---

## Task 3: inject.py / capture.py のキャッシュ・ログパスを PLUGIN_DATA 対応

**Files:**
- Modify: `scripts/inject.py` (line 64: cache_dir)
- Modify: `scripts/_health.py` (health.json パス)

**Step 1: inject.py のキャッシュディレクトリを修正**

L64 を変更:
```python
# Before:
cache_dir = Path(__file__).resolve().parent.parent / "data" / "inject_cache"
# After:
from _common import get_data_root
cache_dir = get_data_root() / "inject_cache"
```

**Step 2: _health.py の health.json パスを修正**

health.json も `get_data_root() / "logs" / "health.json"` に変更。

**Step 3: テスト — inject.py を直接実行して正常終了を確認**

```bash
echo '{}' | .venv/Scripts/python inject.py
```

Expected: エラーなし、exit 0。

---

## Task 4: Node.js ラッパースクリプト作成

**Files:**
- Create: `scripts/setup.mjs`
- Create: `scripts/run-inject.mjs`
- Create: `scripts/run-capture.mjs`
- Create: `scripts/run-search.mjs`

**Step 1: 共通ヘルパー — `_run.mjs` を作成**

```javascript
// scripts/_run.mjs
import { execFileSync } from "child_process";
import { existsSync } from "fs";
import { join } from "path";
import { platform } from "os";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export const PLUGIN_ROOT = join(__dirname, "..");
export const PLUGIN_DATA = process.env.CLAUDE_PLUGIN_DATA || PLUGIN_ROOT;

export function getVenvPython() {
  const venvDir = join(PLUGIN_DATA, ".venv");
  return platform() === "win32"
    ? join(venvDir, "Scripts", "python.exe")
    : join(venvDir, "bin", "python");
}

export function runPython(scriptName, { stdin, timeout } = {}) {
  const py = getVenvPython();
  if (!existsSync(py)) {
    process.stderr.write(
      `agent-memory: venv not found at ${py}. Run setup first.\n`
    );
    process.exit(0); // Don't block Claude
  }
  const script = join(__dirname, scriptName);
  try {
    const result = execFileSync(py, [script], {
      input: stdin,
      timeout: timeout || 10000,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
    });
    if (result) process.stdout.write(result);
  } catch (e) {
    if (e.stderr) process.stderr.write(e.stderr);
    process.exit(0); // Never block Claude
  }
}
```

**Step 2: `run-inject.mjs` を作成**

```javascript
// scripts/run-inject.mjs
import { runPython } from "./_run.mjs";
let stdin = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (stdin += d));
process.stdin.on("end", () => runPython("inject.py", { stdin, timeout: 4500 }));
```

**Step 3: `run-capture.mjs` を作成**

```javascript
// scripts/run-capture.mjs
import { runPython } from "./_run.mjs";
let stdin = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (stdin += d));
process.stdin.on("end", () => runPython("capture.py", { stdin, timeout: 4500 }));
```

**Step 4: `run-search.mjs` を作成**

```javascript
// scripts/run-search.mjs
import { runPython } from "./_run.mjs";
const query = process.argv.slice(2).join(" ");
runPython("search.py", { stdin: query, timeout: 10000 });
```

**Step 5: `setup.mjs` を作成**

```javascript
// scripts/setup.mjs
import { execFileSync, execSync } from "child_process";
import { existsSync, copyFileSync, readFileSync, mkdirSync } from "fs";
import { join } from "path";
import { platform } from "os";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PLUGIN_ROOT = join(__dirname, "..");
const PLUGIN_DATA = process.env.CLAUDE_PLUGIN_DATA || PLUGIN_ROOT;

function main() {
  // Ensure PLUGIN_DATA directories exist
  for (const d of ["logs", "inject_cache"]) {
    mkdirSync(join(PLUGIN_DATA, d), { recursive: true });
  }

  // Check if venv needs (re)build by comparing pyproject.toml
  const srcManifest = join(PLUGIN_ROOT, "scripts", "pyproject.toml");
  const dstManifest = join(PLUGIN_DATA, "pyproject.toml");
  const dstLock = join(PLUGIN_DATA, "uv.lock");
  const srcLock = join(PLUGIN_ROOT, "scripts", "uv.lock");

  let needsBuild = false;
  if (!existsSync(join(PLUGIN_DATA, ".venv"))) {
    needsBuild = true;
  } else if (!existsSync(dstManifest)) {
    needsBuild = true;
  } else {
    const src = readFileSync(srcManifest, "utf8");
    const dst = readFileSync(dstManifest, "utf8");
    if (src !== dst) needsBuild = true;
  }

  if (!needsBuild) return;

  // Copy pyproject.toml + uv.lock to PLUGIN_DATA
  copyFileSync(srcManifest, dstManifest);
  if (existsSync(srcLock)) copyFileSync(srcLock, dstLock);

  // Find uv or fall back to python -m venv + pip
  try {
    execFileSync("uv", ["sync", "--project", PLUGIN_DATA], {
      stdio: "pipe",
      timeout: 120000,
    });
    process.stderr.write("agent-memory: venv created via uv sync\n");
  } catch {
    // Fallback: python -m venv + pip
    const py = findPython();
    if (!py) {
      process.stderr.write("agent-memory: Python 3 not found\n");
      return;
    }
    const venvDir = join(PLUGIN_DATA, ".venv");
    if (!existsSync(venvDir)) {
      execFileSync(py, ["-m", "venv", venvDir], { stdio: "pipe" });
    }
    const pip =
      platform() === "win32"
        ? join(venvDir, "Scripts", "python.exe")
        : join(venvDir, "bin", "python");
    execFileSync(pip, ["-m", "pip", "install", "-r", srcManifest], {
      stdio: "pipe",
      timeout: 120000,
    });
    process.stderr.write("agent-memory: venv created via pip\n");
  }

  // Copy ONNX model to PLUGIN_DATA if not present
  const modelSrc = join(PLUGIN_ROOT, "models", "ruri-v3-30m");
  const modelDst = join(PLUGIN_DATA, "models", "ruri-v3-30m");
  if (existsSync(modelSrc) && !existsSync(modelDst)) {
    mkdirSync(modelDst, { recursive: true });
    for (const f of ["model.onnx", "tokenizer.json"]) {
      const s = join(modelSrc, f);
      if (existsSync(s)) copyFileSync(s, join(modelDst, f));
    }
  }
}

function findPython() {
  const candidates =
    platform() === "win32"
      ? ["py", "python3", "python"]
      : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      const v = execFileSync(cmd, ["--version"], { encoding: "utf8" });
      if (v.includes("Python 3")) return cmd;
    } catch {}
  }
  return null;
}

main();
```

**Step 6: テスト — setup.mjs が venv を PLUGIN_DATA に作成することを確認**

```bash
mkdir -p /tmp/test-plugin-data
CLAUDE_PLUGIN_DATA=/tmp/test-plugin-data node scripts/setup.mjs
ls /tmp/test-plugin-data/.venv/
```

Expected: `.venv/` ディレクトリが作成される。

---

## Task 5: hooks.json を Node.js ラッパー呼び出しに変更

**Files:**
- Modify: `hooks/hooks.json`

**Step 1: hooks.json を書き換え**

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/setup.mjs\"",
            "timeout": 120000
          }
        ]
      },
      {
        "matcher": "startup|resume",
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/run-inject.mjs\"",
            "timeout": 5000
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node \"${CLAUDE_PLUGIN_ROOT}/scripts/run-capture.mjs\"",
            "timeout": 5000
          }
        ]
      }
    ]
  }
}
```

**Step 2: テスト — hooks.json の構文チェック**

```bash
node -e "JSON.parse(require('fs').readFileSync('hooks/hooks.json','utf8')); console.log('Valid JSON')"
```

---

## Task 6: .gitignore / Git LFS 設定

**Files:**
- Modify: `.gitignore`
- Create: `.gitattributes`

**Step 1: .gitignore を更新**

```
# Runtime data (lives in CLAUDE_PLUGIN_DATA)
data/memory.db
data/inject_cache/
data/path_cache.json
logs/

# Python
scripts/.venv/
scripts/__pycache__/
*.pyc

# Keep structure
!data/.gitkeep
!logs/.gitkeep
```

**Step 2: .gitattributes を作成（Git LFS）**

```
models/ruri-v3-30m/model.onnx filter=lfs diff=lfs merge=lfs -text
```

**Step 3: models/ を .gitignore から除外**

現在 `models/ruri-v3-30m/` が .gitignore されている。LFS トラッキングするため除外を解除。

**Step 4: uv.lock を .gitignore から除外**

配布時にロックファイルが必要なので `.gitignore` から `uv.lock` を削除。

---

## Task 7: _embedder.py と _health.py のパスを PLUGIN_DATA 対応

**Files:**
- Modify: `scripts/_embedder.py` (model_path 解決)
- Modify: `scripts/capture.py` (model_path 解決)
- Modify: `scripts/search.py` (model_path 解決)

**Step 1: capture.py L95-96 のモデルパス解決を修正**

```python
# Before:
model_path = (Path(__file__).resolve().parent.parent
              / vec_config.get("model_path", "models/ruri-v3-30m"))
# After:
from _common import get_data_root
model_path = get_data_root() / vec_config.get("model_path", "models/ruri-v3-30m")
```

search.py にも同様の修正。

---

## Task 8: 統合テスト

**Step 1: PLUGIN_DATA を使った inject テスト**

```bash
echo '{"session_id":"test-123","cwd":"/tmp","transcript_path":""}' | \
  CLAUDE_PLUGIN_DATA=/path/to/data node scripts/run-inject.mjs
```

**Step 2: PLUGIN_DATA を使った capture テスト**

実際の JSONL ファイルで:
```bash
echo '{"session_id":"test-123","cwd":"/tmp","transcript_path":"/path/to/real.jsonl"}' | \
  CLAUDE_PLUGIN_DATA=/path/to/data node scripts/run-capture.mjs
```

**Step 3: search テスト**

```bash
CLAUDE_PLUGIN_DATA=/path/to/data node scripts/run-search.mjs "テスト検索"
```

**Step 4: Claude Code で実際にセッション開始/終了して動作確認**

settings.json の個人 hook を一時的にコメントアウトし、hooks.json 経由のみで動くことを確認。

---

## Task 9: 動作確認後コミット

```bash
git add -A
git commit -m "feat: cross-platform distribution support (Node.js wrapper + PLUGIN_DATA)"
```

**ロールバック手順:** 動かない場合は `git checkout .` で全復元。

---

## 変更しないファイル一覧

- `scripts/inject.py` — キャッシュパス修正のみ（ロジック不変）
- `scripts/capture.py` — モデルパス修正のみ（ロジック不変）
- `scripts/search.py` — モデルパス修正のみ
- `scripts/_db.py` — 変更なし
- `scripts/_parser.py` — 変更なし
- `scripts/_tokenizer.py` — 変更なし
- `scripts/_path_resolver.py` — 変更なし
- `scripts/backfill_*.py` — 変更なし
- `skills/` — 変更なし
- `.claude-plugin/plugin.json` — 変更なし
