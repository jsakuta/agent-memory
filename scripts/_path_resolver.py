"""
.claude/projects/<dirname> -> 実リポジトリパス の解決とキャッシュ。

戦略:
1. path_cache.json にキャッシュがあればそれを返す
2. なければ .claude/projects/<dirname>/*.jsonl から "cwd" を grep
3. 見つかったらキャッシュに書き込み
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _common import get_data_root

_PROJECTS_RE = re.compile(r"[/\\]\.claude[/\\]projects[/\\]([^/\\]+)", re.IGNORECASE)


def get_cache_path() -> Path:
    """キャッシュファイルのパスを返す。"""
    return get_data_root() / "data" / "path_cache.json"


def load_cache() -> dict[str, str]:
    """data/path_cache.json を読む。失敗時は空 dict。"""
    try:
        return json.loads(get_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache: dict[str, str]) -> None:
    """キャッシュを書き込む。失敗は無視。"""
    try:
        p = get_cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def extract_dirname(cwd: str) -> str | None:
    """cwd が .claude/projects/<dirname> 配下なら dirname を返す。"""
    m = _PROJECTS_RE.search(cwd)
    return m.group(1) if m else None


def resolve_real_path(dirname: str) -> str | None:
    """JSONL から実 cwd を探す。"""
    home = Path.home()
    projects_dir = home / ".claude" / "projects" / dirname
    if not projects_dir.is_dir():
        return None

    for jsonl_file in projects_dir.glob("*.jsonl"):
        try:
            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    if '"cwd"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    cwd_val = obj.get("cwd")
                    if not cwd_val:
                        continue
                    # 実パスのみ採用（.claude/projects/ 配下でないこと）
                    if _PROJECTS_RE.search(cwd_val):
                        continue
                    return cwd_val
        except Exception:
            continue
    return None


def resolve_cwd(cwd: str) -> str:
    """
    cwd を受け取り、.claude/projects/ パスなら実パスに変換して返す。
    変換できない場合は元の cwd をそのまま返す。
    """
    dirname = extract_dirname(cwd)
    if dirname is None:
        return cwd

    # 1. キャッシュ確認
    cache = load_cache()
    if dirname in cache:
        return cache[dirname]

    # 2. JSONL から探索
    real_path = resolve_real_path(dirname)
    if real_path is None:
        return cwd

    # 3. キャッシュに保存
    cache[dirname] = real_path
    save_cache(cache)
    return real_path
