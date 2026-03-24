import json
from pathlib import Path
import _common  # noqa: F401 — triggers WMI bypass on Windows

def _health_json_path() -> Path:
    from _common import get_data_root
    log_dir = get_data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "health.json"

def health_check() -> bool:
    """fugashi/sqlite3/onnxruntime の import 検証（重量級）。
    capture.py / search.py で使用。inject.py では使わない。
    失敗時は health.json に連続失敗カウンタを記録。"""
    ok = True
    try:
        import fugashi
        import sqlite_vec
        import onnxruntime
    except ImportError:
        ok = False
    # health.json 更新
    hp = _health_json_path()
    state = json.loads(hp.read_text()) if hp.exists() else {"consecutive_failures": 0}
    if ok:
        state["consecutive_failures"] = 0
    else:
        state["consecutive_failures"] = state.get("consecutive_failures", 0) + 1
    hp.write_text(json.dumps(state))
    return ok

def read_health_status() -> dict:
    """health.json の読み取りのみ（軽量）。inject.py で使用。
    onnxruntime/fugashi を import しないため <50ms で完了。"""
    hp = _health_json_path()
    if hp.exists():
        return json.loads(hp.read_text())
    return {"consecutive_failures": 0}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    # 1. Health status
    status = read_health_status()
    failures = status.get("consecutive_failures", 0)
    print(f"Health: {'OK' if failures < 3 else 'DEGRADED'} (failures={failures})")

    # 2. Dependencies
    for mod in ["fugashi", "sqlite_vec", "onnxruntime", "tokenizers"]:
        try:
            __import__(mod)
            print(f"{mod}: OK")
        except Exception as e:
            print(f"{mod}: FAIL ({e})")

    # 3. DB integrity
    from _common import get_db_path, load_config
    from _db import get_connection
    config = load_config()
    db_path = get_db_path(config)
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        sys.exit(1)
    conn = get_connection(db_path)
    s = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    c = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    f = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    v = conn.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0]
    print(f"Sessions: {s}, Chunks: {c}, FTS: {f}, Vec: {v}")
    if c != f:
        print("WARNING: FTS5 mismatch - REBUILD needed")
    if c != v:
        print(f"INFO: Vec backfill pending ({c - v} chunks)")
    if c == f:
        print("Integrity: OK")
    conn.close()
