"""PM_Vault外の .claude/projects/ からセッションを一括取込する。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import load_config, get_db_path, get_logger
from _db import get_connection
from capture import process_session
from _path_resolver import resolve_real_path


def main():
    logger = get_logger("backfill_external")
    config = load_config()
    db_path = get_db_path(config)

    conn = get_connection(db_path)
    existing = set(
        row[0] for row in conn.execute("SELECT session_id FROM sessions").fetchall()
    )
    conn.close()

    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        print("No .claude/projects/ directory found")
        return

    total_new = 0
    for proj_dir in sorted(projects_dir.iterdir()):
        if not proj_dir.is_dir():
            continue

        dirname = proj_dir.name

        # PM_Vault は既に取込済み（スキップ）
        if dirname.casefold() == "c--pm-vault":
            continue

        # 実パスを解決
        real_path = resolve_real_path(dirname)
        cwd = real_path or str(proj_dir)

        jsonl_files = sorted(proj_dir.glob("*.jsonl"))
        new_count = 0
        for jsonl_file in jsonl_files:
            session_id = jsonl_file.stem
            if session_id in existing:
                continue

            try:
                process_session(
                    jsonl_path=str(jsonl_file),
                    session_id=session_id,
                    cwd=cwd,
                    config=config,
                    time_limit=None,
                )
                new_count += 1
                existing.add(session_id)
            except Exception as e:
                logger.error(f"Failed to process {session_id}: {e}")
                print(f"  ERROR: {session_id}: {e}")

        if new_count:
            print(f"  {dirname}: {new_count} new sessions")
            total_new += new_count

    print(f"\nTotal: {total_new} new sessions imported")

    # Show final stats
    conn = get_connection(db_path)
    stats = conn.execute(
        "SELECT project, COUNT(*) FROM sessions GROUP BY project ORDER BY COUNT(*) DESC"
    ).fetchall()
    print("\nProject distribution:")
    for proj, cnt in stats:
        print(f"  {proj or '(empty)'}: {cnt}")
    total = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"\nTotal: {total} sessions, {chunks} chunks")
    conn.close()


if __name__ == "__main__":
    main()
