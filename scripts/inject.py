"""SessionStart hook: recency注入。
CRITICAL: fugashi, onnxruntime, _embedder, _tokenizer は import しない。
timeout 3s 制約のため最小 import で動作する。
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# sys.path for sibling imports
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    read_hook_input,
    load_config,
    get_db_path,
    resolve_project,
    get_logger,
    compute_recency,
)
from _health import read_health_status


def _parse_iso_days_ago(iso_str: str | None) -> float:
    """ISO 8601 文字列を受け取り、現在からの経過日数を返す。
    パース失敗時は 7.0 (安全なフォールバック)。"""
    if not iso_str:
        return 14.0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max((now - dt).total_seconds() / 86400, 0.0)
    except (ValueError, TypeError):
        return 7.0


def main():
    logger = get_logger("inject")

    try:
        hook_input = read_hook_input()
        session_id = hook_input.get("session_id", "")
        cwd = hook_input.get("cwd", "")
        transcript_path = hook_input.get("transcript_path", "")

        if not session_id:
            sys.exit(0)

        config = load_config()

        # ── Health status check (light — reads JSON only) ──
        health = read_health_status()
        health_warning = ""
        if health.get("consecutive_failures", 0) >= 3:
            health_warning = (
                "\n⚠️ session-memory: 依存パッケージの読み込みに連続失敗中。"
                "`/memory-health` で診断してください。"
            )

        # ── Cleanup stale cache files (>24h) ──
        cache_dir = Path(__file__).resolve().parent.parent / "data" / "inject_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            now = time.time()
            for f in cache_dir.glob("*.json"):
                if now - f.stat().st_mtime > 86400:
                    f.unlink(missing_ok=True)
        except OSError:
            pass

        # ── Idempotency check (inject_cache TTL) ──
        cache_file = cache_dir / f"{session_id}.json"
        ttl = config.get("inject_cache_ttl_seconds", 300)

        if cache_file.exists():
            try:
                cache_data = json.loads(cache_file.read_text(encoding="utf-8"))
                if time.time() - cache_data.get("timestamp", 0) < ttl:
                    # Cache hit — still inject health warning if needed
                    if health_warning:
                        _emit(health_warning.strip())
                    sys.exit(0)
            except (json.JSONDecodeError, KeyError):
                pass  # Corrupted cache — proceed normally

        # ── DB existence check ──
        db_path = get_db_path(config)
        if not db_path.exists():
            if health_warning:
                _emit(health_warning.strip())
            sys.exit(0)

        from _db import get_connection

        conn = get_connection(db_path)

        # ── Backfill flag (set only, no heavy processing) ──
        if transcript_path and session_id:
            try:
                conn.execute(
                    """INSERT INTO processing_state (session_id, backfill_requested)
                       VALUES (?, 1)
                       ON CONFLICT(session_id) DO UPDATE SET backfill_requested = 1""",
                    (session_id,),
                )
                conn.commit()
            except Exception:
                pass  # Non-critical

        # ── Project resolution (longest prefix match) ──
        project_map = config.get("projects", {})
        project = resolve_project(cwd, project_map) if cwd else None

        # ── Recent sessions ──
        recency_count = config.get("recency_count", 3)
        token_budget = config.get("token_budget", 2000)

        if project:
            recent_sessions = conn.execute(
                """SELECT session_id, last_updated FROM sessions
                   WHERE project = ?
                   ORDER BY last_updated DESC
                   LIMIT ?""",
                (project, recency_count),
            ).fetchall()
        else:
            recent_sessions = conn.execute(
                """SELECT session_id, last_updated FROM sessions
                   ORDER BY last_updated DESC
                   LIMIT ?""",
                (recency_count,),
            ).fetchall()

        if not recent_sessions:
            conn.close()
            _write_cache(cache_file)
            if health_warning:
                _emit(health_warning.strip())
            sys.exit(0)

        # ── Representative chunk selection ──
        half_life = config.get("half_life_days", 14.0)
        floor = config.get("recency_floor", 0.01)
        boost_factor = config.get("access_boost_factor", 0.3)

        context_parts: list[str] = []
        total_est_tokens = 0

        for sid, _last_updated in recent_sessions:
            chunks = conn.execute(
                """SELECT user_text, assistant_text, timestamp,
                          hit_count, last_accessed, char_count
                   FROM chunks
                   WHERE session_id = ?
                   ORDER BY timestamp DESC
                   LIMIT 5""",
                (sid,),
            ).fetchall()

            if not chunks:
                continue

            # Pick best chunk by recency score
            best_chunk = None
            best_score = -1.0
            for chunk in chunks:
                _user, _asst, ts, hit_count, last_accessed, _cc = chunk
                access_ref = last_accessed or ts
                days = _parse_iso_days_ago(access_ref)
                score = compute_recency(
                    days, hit_count or 0, half_life, floor, boost_factor
                )
                if score > best_score:
                    best_score = score
                    best_chunk = chunk

            if best_chunk is None:
                continue

            user_text, assistant_text, ts, _hc, _la, char_count = best_chunk
            chunk_chars = char_count or len(user_text or "") + len(assistant_text or "")
            est_tokens = chunk_chars // 2

            if total_est_tokens + est_tokens > token_budget:
                break

            total_est_tokens += est_tokens

            # Format context line
            summary = (assistant_text or "")[:200]
            ts_label = ts or "unknown"
            if user_text:
                user_preview = user_text[:100]
                context_parts.append(f"[{ts_label}] Q: {user_preview}... → {summary}...")
            else:
                context_parts.append(f"[{ts_label}] {summary}...")

        conn.close()

        # ── Build additionalContext ──
        context_text = ""
        if context_parts:
            context_text = "📝 直近のセッション記憶:\n" + "\n".join(context_parts)

        if health_warning:
            context_text = (
                (context_text + health_warning) if context_text else health_warning.strip()
            )

        if context_text:
            _emit(context_text)
            logger.info(f"Injected {len(context_parts)} memories for {session_id[:12]}...")
        else:
            logger.info(f"No memories to inject for {session_id[:12]}...")

        # ── Update cache ──
        _write_cache(cache_file)

    except Exception as e:
        logger.error(f"inject.py error: {e}")
        sys.exit(0)  # Never block session


def _emit(additional_context: str) -> None:
    """SessionStart hookSpecificOutput を stdout に出力。"""
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }
    print(json.dumps(output, ensure_ascii=False))


def _write_cache(cache_file: Path) -> None:
    """冪等キャッシュファイルを書き込む。"""
    try:
        cache_file.write_text(
            json.dumps({"timestamp": time.time()}), encoding="utf-8"
        )
    except OSError:
        pass


if __name__ == "__main__":
    main()
