---
name: mem-health
description: >
  agent-memory プラグインの状態診断と復旧。
  トリガー: 「メモリの状態」「記憶が動いてない」「memory health」
  「mem-health」「/mem-health」「記憶プラグイン」「セッション記憶の状態」
  「hookが動いてない」「メモリが壊れた」「検索できない」
---

# agent-memory ヘルス診断

プラグインの動作状態を診断し、問題があれば復旧手順を案内する。

## クイック診断（まずこれを実行）

```bash
cd ~/.claude/plugins/local/agent-memory/scripts
.venv/Scripts/python -c "
import sys; sys.path.insert(0, '.')
import _common  # WMI bypass (Python 3.13 platform.system() hang fix)
import json
from pathlib import Path
from _common import get_db_path, load_config
from _db import get_connection

# 1. Health status
health_path = Path(__file__).resolve().parent.parent / 'logs' / 'health.json'
if health_path.exists():
    h = json.loads(health_path.read_text())
    failures = h.get('consecutive_failures', 0)
    print(f'Health: {\"OK\" if failures < 3 else \"DEGRADED\"} (failures={failures})')
else:
    print('Health: health.json not found')

# 2. Dependencies
for mod in ['fugashi', 'sqlite_vec', 'onnxruntime', 'tokenizers']:
    try:
        __import__(mod)
        print(f'{mod}: OK')
    except Exception as e:
        print(f'{mod}: FAIL ({e})')

# 3. DB integrity
config = load_config()
conn = get_connection(get_db_path(config))
s = conn.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]
c = conn.execute('SELECT COUNT(*) FROM chunks').fetchone()[0]
f = conn.execute('SELECT COUNT(*) FROM chunks_fts').fetchone()[0]
v = conn.execute('SELECT COUNT(*) FROM vec_chunks').fetchone()[0]
print(f'Sessions: {s}, Chunks: {c}, FTS: {f}, Vec: {v}')
if c != f: print('WARNING: FTS5 mismatch - REBUILD needed')
if c != v: print('WARNING: Vec mismatch - backfill_vec.py needed')
if c == f == v: print('Integrity: OK')
conn.close()
"
```

上記の出力を読み、問題があれば以下の復旧手順を実行する。

## 復旧手順

### FTS5不整合 → REBUILD

```bash
.venv/Scripts/python -c "
import sys; sys.path.insert(0, '.')
from _common import get_db_path, load_config
from _db import get_connection
conn = get_connection(get_db_path(load_config()))
conn.execute(\"INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')\")
conn.commit(); print('FTS5 rebuilt'); conn.close()
"
```

### Vec不整合 → backfill

```bash
.venv/Scripts/python backfill_vec.py
```

### venv破損 → 再構築

```bash
cd ~/.claude/plugins/local/agent-memory/scripts
rm -rf .venv
uv sync --no-install-project
```

### ログ詳細確認

```bash
tail -30 ~/.claude/plugins/local/agent-memory/logs/capture.log
tail -30 ~/.claude/plugins/local/agent-memory/logs/inject.log
```
