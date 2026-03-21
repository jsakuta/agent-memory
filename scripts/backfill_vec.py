"""Batch backfill Vec embeddings for existing chunks."""
import sys
import struct
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import load_config, get_db_path, get_logger
from _db import get_connection
from _embedder import Embedder


def backfill(batch_size: int = 100):
    """Find chunks without vec embeddings and generate them."""
    logger = get_logger("backfill")
    config = load_config()

    db_path = get_db_path(config)
    if not db_path.exists():
        print("DB not found. Run capture.py first.")
        return

    vec_config = config.get("vec", {})
    model_path = Path(__file__).resolve().parent.parent / vec_config.get(
        "model_path", "models/ruri-v3-30m"
    )
    embedder = Embedder(str(model_path))

    if not embedder.available:
        print("Embedder not available. Check model files.")
        return

    conn = get_connection(db_path)

    # Find chunks without vec embeddings
    missing = conn.execute(
        """
        SELECT c.id, c.user_text, c.assistant_text
        FROM chunks c
        LEFT JOIN vec_chunks v ON v.rowid = c.id
        WHERE v.rowid IS NULL
        ORDER BY c.id
    """
    ).fetchall()

    total = len(missing)
    if total == 0:
        print("All chunks already have vec embeddings.")
        conn.close()
        return

    print(f"Backfilling {total} chunks...")

    success = 0
    errors = 0
    start = time.time()

    for i, (rowid, user_text, assistant_text) in enumerate(missing):
        combined = ((user_text or "") + " " + (assistant_text or "")).strip()
        if not combined:
            continue

        embedding = embedder.embed(combined)
        if embedding:
            try:
                vec_bytes = struct.pack(f"{len(embedding)}f", *embedding)
                conn.execute(
                    "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                    (rowid, vec_bytes),
                )
                success += 1
            except Exception as e:
                errors += 1
                logger.warning(f"Vec insert error for chunk {rowid}: {e}")

        # Progress
        if (i + 1) % batch_size == 0 or (i + 1) == total:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            print(
                f"  {i+1}/{total} ({rate:.1f} chunks/sec, {success} ok, {errors} err)"
            )
            conn.commit()

    conn.commit()
    conn.close()

    elapsed = time.time() - start
    print(f"Done: {success}/{total} embeddings in {elapsed:.1f}s")


if __name__ == "__main__":
    backfill()
