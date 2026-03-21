import json
from pathlib import Path

def _health_json_path() -> Path:
    return Path(__file__).resolve().parent.parent / "logs" / "health.json"

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
