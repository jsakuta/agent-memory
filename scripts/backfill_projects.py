"""既存セッションの project カラムを cwd から再計算する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import load_config, get_db_path, resolve_project
from _db import get_connection


def main():
    recalc_all = "--all" in sys.argv
    config = load_config()
    db_path = get_db_path(config)
    conn = get_connection(db_path)
    project_map = config.get("projects", {})

    if recalc_all:
        rows = conn.execute(
            "SELECT session_id, cwd FROM sessions"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT session_id, cwd FROM sessions WHERE project = '' OR project IS NULL"
        ).fetchall()

    updated = 0
    for session_id, cwd in rows:
        if not cwd:
            continue
        project = resolve_project(cwd, project_map)
        if project:
            conn.execute(
                "UPDATE sessions SET project = ? WHERE session_id = ?",
                (project, session_id),
            )
            updated += 1

    conn.commit()
    total = len(rows)
    print(f"Backfill complete: {updated}/{total} sessions updated")

    stats = conn.execute(
        "SELECT project, COUNT(*) FROM sessions GROUP BY project ORDER BY COUNT(*) DESC"
    ).fetchall()
    for proj, cnt in stats:
        print(f"  {proj or '(empty)'}: {cnt}")

    conn.close()


if __name__ == "__main__":
    main()
