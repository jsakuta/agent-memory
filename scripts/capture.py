"""Stop hook: JSONL差分 → SQLite + FTS5 + Vec。
process_session() は一括取り込み (Task 16) でも使用する公開API。
"""
import json
import sys
import time
import struct
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import read_hook_input, load_config, get_db_path, resolve_project, get_logger
from _health import health_check
from _db import get_connection, init_db
from _parser import parse_jsonl
from _tokenizer import tokenize

# Try to import embedder (optional — FTS5-only mode if unavailable)
try:
    from _embedder import Embedder
    _embedder_available = True
except ImportError:
    _embedder_available = False


def process_session(jsonl_path: str, session_id: str, cwd: str,
                    config: dict | None = None, time_limit: float | None = None):
    """外部呼び出し用 API。一括取り込み (Task 16) で使用。

    Args:
        jsonl_path: JSONL transcript file path
        session_id: Session UUID
        cwd: Working directory at session time
        config: Optional pre-loaded config (avoids re-reading settings.toml)
        time_limit: Optional time limit in seconds (None = no limit)
    """
    logger = get_logger("capture")

    if not jsonl_path or not Path(jsonl_path).exists():
        return

    if config is None:
        config = load_config()

    db_path = get_db_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    init_db(conn)

    start_time = time.time()

    # Project resolution
    project_map = config.get("projects", {})
    project = resolve_project(cwd, project_map) if cwd else None

    # Get last processed offset
    row = conn.execute(
        "SELECT last_processed_offset FROM processing_state WHERE session_id = ?",
        (session_id,)
    ).fetchone()
    offset = row[0] if row else 0

    # Check file size for changes
    file_size = Path(jsonl_path).stat().st_size
    if file_size <= offset:
        conn.close()
        return  # No new data

    # Parse JSONL
    try:
        exchanges, new_offset = parse_jsonl(jsonl_path, offset)
    except Exception as e:
        logger.error(f"parse_jsonl error for {session_id}: {e}")
        conn.close()
        return

    if not exchanges:
        # Update offset even if no exchanges (skip metadata-only lines)
        conn.execute("""
            INSERT INTO processing_state (session_id, last_processed_offset)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET last_processed_offset = ?
        """, (session_id, new_offset, new_offset))
        conn.commit()
        conn.close()
        return

    # Load embedder if available
    embedder = None
    if _embedder_available:
        try:
            vec_config = config.get("vec", {})
            if vec_config.get("enabled", True):
                model_path = (Path(__file__).resolve().parent.parent
                              / vec_config.get("model_path", "models/ruri-v3-30m"))
                embedder = Embedder(str(model_path))
                if not embedder.available:
                    embedder = None
        except Exception:
            embedder = None

    # Get existing chunk count for chunk_index
    existing_count = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE session_id = ?", (session_id,)
    ).fetchone()[0]

    now_iso = datetime.now(timezone.utc).isoformat()

    # Process exchanges in single transaction
    try:
        for i, ex in enumerate(exchanges):
            # Time guard
            if time_limit and (time.time() - start_time) > time_limit:
                logger.info(f"Time limit reached after {i} exchanges for {session_id}")
                break

            user_text = ex.user_text or ""
            assistant_text = "\n".join(ex.assistant_texts) if ex.assistant_texts else ""

            if not user_text and not assistant_text:
                continue

            chunk_index = existing_count + i
            char_count = len(user_text) + len(assistant_text)

            # Tokenize for FTS5
            user_tokenized = tokenize(user_text) if user_text else ""
            assistant_tokenized = tokenize(assistant_text) if assistant_text else ""

            # Insert into chunks
            cursor = conn.execute("""
                INSERT INTO chunks (
                    session_id, chunk_index, user_text, assistant_text,
                    timestamp, hit_count, last_accessed,
                    git_branch, files_touched, tools_used,
                    char_count, api_tokens, is_compact_summary
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session_id, chunk_index, user_text, assistant_text,
                ex.timestamp or now_iso, now_iso,
                ex.git_branch or "",
                json.dumps(sorted(ex.files_touched)) if ex.files_touched else "[]",
                json.dumps(sorted(ex.tools_used)) if ex.tools_used else "[]",
                char_count, ex.api_tokens,
                1 if ex.is_compact_summary else 0,
            ))
            rowid = cursor.lastrowid

            # Insert into FTS5 (contentless — needs explicit rowid)
            conn.execute(
                "INSERT INTO chunks_fts(rowid, user_tokenized, assistant_tokenized) "
                "VALUES (?, ?, ?)",
                (rowid, user_tokenized, assistant_tokenized)
            )

            # Insert into vec_chunks if embedder available
            if embedder:
                try:
                    combined_text = (user_text + " " + assistant_text).strip()
                    embedding = embedder.embed(combined_text)
                    if embedding:
                        # Pack as float32 binary
                        vec_bytes = struct.pack(f'{len(embedding)}f', *embedding)
                        conn.execute(
                            "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                            (rowid, vec_bytes)
                        )
                except Exception as e:
                    logger.warning(f"Vec embedding failed for chunk {rowid}: {e}")

        # UPSERT session
        timestamp_first = exchanges[0].timestamp if exchanges else now_iso
        conn.execute("""
            INSERT INTO sessions (session_id, project, started_at, last_updated,
                                  message_count, cwd)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                last_updated = excluded.last_updated,
                message_count = message_count + excluded.message_count
        """, (
            session_id, project or "",
            timestamp_first, now_iso,
            len(exchanges), cwd or "",
        ))

        # Update processing state
        conn.execute("""
            INSERT INTO processing_state (session_id, last_processed_offset,
                                          backfill_requested)
            VALUES (?, ?, 0)
            ON CONFLICT(session_id) DO UPDATE SET
                last_processed_offset = ?,
                backfill_requested = 0
        """, (session_id, new_offset, new_offset))

        conn.commit()
        logger.info(
            f"Captured {len(exchanges)} exchanges for {session_id} "
            f"(offset {offset}->{new_offset})"
        )

    except Exception as e:
        logger.error(f"capture error for {session_id}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


if __name__ == "__main__":
    logger = get_logger("capture")

    try:
        # Health check (heavy — imports fugashi/sqlite-vec/onnxruntime)
        if not health_check():
            logger.warning("Health check failed, skipping capture")
            sys.exit(0)

        hook_input = read_hook_input()
        transcript_path = hook_input.get("transcript_path", "")
        session_id = hook_input.get("session_id", "")
        cwd = hook_input.get("cwd", "")

        if not transcript_path or not session_id:
            sys.exit(0)

        process_session(
            jsonl_path=transcript_path,
            session_id=session_id,
            cwd=cwd,
            time_limit=4.5,  # 5s hook timeout - 0.5s margin
        )

    except Exception as e:
        logger.error(f"capture.py fatal: {e}")

    sys.exit(0)  # Never block session
