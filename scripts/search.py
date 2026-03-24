"""Search engine: FTS5+Vec hybrid with RRF merge + 2-factor reranking."""

import json
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import compute_recency, get_data_root, get_db_path, get_logger, load_config
from _db import get_connection

# Optional embedder
try:
    from _embedder import Embedder

    _embedder_available = True
except ImportError:
    _embedder_available = False


def parse_query(raw_query: str) -> tuple[str | None, str]:
    """Parse 'project:01-CBK query text' format."""
    project = None
    query = raw_query.strip()
    if query.startswith("project:"):
        parts = query.split(None, 1)
        project = parts[0].split(":", 1)[1]
        query = parts[1] if len(parts) > 1 else ""
    return project, query


def search(raw_query: str, config: dict | None = None) -> list[dict]:
    """Main search function. Returns list of result dicts."""
    logger = get_logger("search")

    if config is None:
        config = load_config()

    db_path = get_db_path(config)
    if not db_path.exists():
        return []

    conn = get_connection(db_path)

    project, query = parse_query(raw_query)
    if not query:
        conn.close()
        return []

    fts_limit = config.get("fts_candidate_limit", 100)
    result_limit = config.get("result_limit", 20)
    half_life = config.get("half_life_days", 14.0)
    floor_val = config.get("recency_floor", 0.01)
    boost_factor = config.get("access_boost_factor", 0.3)
    k = 60  # RRF constant

    # --- FTS5 trigram search (3-stage fallback) ---
    fts_results = {}  # rowid -> rank (1-indexed)

    # Project filter subquery
    if project:
        project_filter = (
            "AND c.session_id IN "
            "(SELECT session_id FROM sessions WHERE project = ?)"
        )
        project_params = [project]
    else:
        project_filter = ""
        project_params = []

    # Split query into words for multi-term search
    words = [w for w in query.split() if len(w) >= 3]

    # Stage 0: Phrase match (全体を部分文字列として検索)
    if len(query) >= 3:
        fts_query = f'"{query}"'
        try:
            rows = conn.execute(
                f"""
                SELECT chunks_fts.rowid, rank
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                {project_filter}
                ORDER BY rank
                LIMIT ?
            """,
                [fts_query] + project_params + [fts_limit],
            ).fetchall()
            for rank_idx, (rowid, _score) in enumerate(rows, 1):
                fts_results[rowid] = rank_idx
        except Exception as e:
            logger.warning(f"FTS Stage 0 (phrase) error: {e}")

    # Stage 1: OR search (各単語を個別にtrigram検索、フレーズで不足なら)
    if len(fts_results) < 5 and words:
        or_query = " OR ".join(f'"{w}"' for w in words)
        try:
            rows = conn.execute(
                f"""
                SELECT chunks_fts.rowid, rank
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                WHERE chunks_fts MATCH ?
                {project_filter}
                ORDER BY rank
                LIMIT ?
            """,
                [or_query] + project_params + [fts_limit],
            ).fetchall()
            for rank_idx, (rowid, _score) in enumerate(rows, 1):
                if rowid not in fts_results:
                    fts_results[rowid] = rank_idx
        except Exception as e:
            logger.warning(f"FTS Stage 1 (OR) error: {e}")

    # Stage 2: LIKE fallback (短いクエリ or trigram MATCHで結果なし)
    if not fts_results:
        try:
            like_pattern = f"%{query}%"
            rows = conn.execute(
                f"""
                SELECT c.id FROM chunks c
                WHERE (c.user_text LIKE ? OR c.assistant_text LIKE ?)
                {project_filter}
                ORDER BY c.timestamp DESC
                LIMIT ?
            """,
                [like_pattern, like_pattern] + project_params + [fts_limit],
            ).fetchall()
            for rank_idx, (rowid,) in enumerate(rows, 1):
                fts_results[rowid] = rank_idx
        except Exception as e:
            logger.warning(f"FTS Stage 2 (LIKE) error: {e}")

    # --- Vec search ---
    vec_results = {}  # rowid -> rank (1-indexed)
    embedder = None

    if _embedder_available:
        try:
            vec_config = config.get("vec", {})
            if vec_config.get("enabled", True):
                model_path = (
                    get_data_root()
                    / vec_config.get("model_path", "models/ruri-v3-130m")
                )
                embedder = Embedder(str(model_path), max_length=8192)
        except Exception:
            pass

    if embedder and embedder.available:
        query_vec = embedder.embed(query, prefix="検索クエリ: ")
        if query_vec:
            vec_bytes = struct.pack(f"{len(query_vec)}f", *query_vec)
            try:
                # KNN search
                if project:
                    rows = conn.execute(
                        """
                        SELECT v.rowid, v.distance
                        FROM vec_chunks v
                        JOIN chunks c ON c.id = v.rowid
                        JOIN sessions s ON s.session_id = c.session_id
                        WHERE v.embedding MATCH ? AND k = ?
                        AND s.project = ?
                        ORDER BY v.distance
                    """,
                        [vec_bytes, fts_limit, project],
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT v.rowid, v.distance
                        FROM vec_chunks v
                        WHERE v.embedding MATCH ? AND k = ?
                        ORDER BY v.distance
                    """,
                        [vec_bytes, fts_limit],
                    ).fetchall()

                for rank_idx, (rowid, _dist) in enumerate(rows, 1):
                    vec_results[rowid] = rank_idx
            except Exception as e:
                logger.warning(f"Vec search error: {e}")

    # --- RRF merge ---
    all_rowids = set(fts_results.keys()) | set(vec_results.keys())
    if not all_rowids:
        conn.close()
        return []

    missing_rank = fts_limit + 1
    rrf_scores = {}
    for rowid in all_rowids:
        fts_rank = fts_results.get(rowid, missing_rank)
        vec_rank = vec_results.get(rowid, missing_rank)
        rrf_scores[rowid] = 1.0 / (k + fts_rank) + 1.0 / (k + vec_rank)

    # --- Fetch chunk data for reranking ---
    rowid_list = list(all_rowids)
    placeholders = ",".join("?" * len(rowid_list))
    chunks_data = conn.execute(
        f"""
        SELECT c.id, c.session_id, c.user_text, c.assistant_text,
               c.timestamp, c.hit_count, c.last_accessed, c.char_count,
               c.git_branch, c.is_compact_summary, s.project
        FROM chunks c
        JOIN sessions s ON s.session_id = c.session_id
        WHERE c.id IN ({placeholders})
    """,
        rowid_list,
    ).fetchall()

    chunk_map = {}
    for row in chunks_data:
        chunk_map[row[0]] = {
            "id": row[0],
            "session_id": row[1],
            "user_text": row[2],
            "assistant_text": row[3],
            "timestamp": row[4],
            "hit_count": row[5] or 0,
            "last_accessed": row[6],
            "char_count": row[7],
            "git_branch": row[8],
            "is_compact_summary": row[9],
            "project": row[10],
        }

    # --- 2-factor reranking ---
    now = datetime.now(timezone.utc)

    # Compute raw scores
    scored = []
    for rowid in all_rowids:
        if rowid not in chunk_map:
            continue
        chunk = chunk_map[rowid]

        relevance_raw = rrf_scores.get(rowid, 0)

        # Recency
        access_time = chunk["last_accessed"] or chunk["timestamp"]
        days = 14.0  # fallback
        if access_time:
            try:
                dt = datetime.fromisoformat(access_time.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    days = (datetime.now() - dt).total_seconds() / 86400
                else:
                    days = (now - dt).total_seconds() / 86400
            except (ValueError, TypeError):
                pass

        recency_raw = compute_recency(
            days, chunk["hit_count"], half_life, floor_val, boost_factor
        )

        scored.append(
            {
                "rowid": rowid,
                "chunk": chunk,
                "relevance_raw": relevance_raw,
                "recency_raw": recency_raw,
            }
        )

    if not scored:
        conn.close()
        return []

    # Min-max normalization
    rel_values = [s["relevance_raw"] for s in scored]
    rec_values = [s["recency_raw"] for s in scored]

    rel_min, rel_max = min(rel_values), max(rel_values)
    rec_min, rec_max = min(rec_values), max(rec_values)

    rel_range = rel_max - rel_min if rel_max > rel_min else 1.0
    rec_range = rec_max - rec_min if rec_max > rec_min else 1.0

    for s in scored:
        rel_norm = (s["relevance_raw"] - rel_min) / rel_range
        rec_norm = (s["recency_raw"] - rec_min) / rec_range
        s["score"] = 0.7 * rel_norm + 0.3 * rec_norm

    # Sort and limit
    scored.sort(key=lambda x: x["score"], reverse=True)
    results = scored[:result_limit]

    # --- Update hit_count and last_accessed (reinforcement) ---
    now_iso = now.isoformat()
    for r in results:
        try:
            conn.execute(
                """
                UPDATE chunks SET hit_count = hit_count + 1, last_accessed = ?
                WHERE id = ?
            """,
                (now_iso, r["rowid"]),
            )
        except Exception:
            pass
    conn.commit()
    conn.close()

    return results


def format_results(results: list[dict]) -> str:
    """Format results as Markdown."""
    if not results:
        return "検索結果なし"

    lines = [f"## 検索結果 ({len(results)}件)\n"]

    for i, r in enumerate(results, 1):
        chunk = r["chunk"]
        score = r["score"]
        user_preview = (chunk["user_text"] or "")[:150].replace("\n", " ")
        assistant_preview = (chunk["assistant_text"] or "")[:200].replace("\n", " ")

        lines.append(
            f"### {i}. [score: {score:.3f}] {chunk['timestamp'] or 'unknown'}"
        )
        if chunk["project"]:
            lines.append(f"**Project:** {chunk['project']}")
        if chunk["session_id"]:
            lines.append(f"**Session:** `{chunk['session_id'][:12]}...`")
        if user_preview:
            lines.append(f"**Q:** {user_preview}")
        if assistant_preview:
            lines.append(f"**A:** {assistant_preview}...")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: search.py <query>", file=sys.stderr)
        sys.exit(1)

    query = sys.argv[1]
    results = search(query)
    print(format_results(results))
