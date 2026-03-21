import json, sys, math, tomllib
from pathlib import Path

def get_plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent

def load_config() -> dict:
    config_path = get_plugin_root() / "config" / "settings.toml"
    with open(config_path, "rb") as f:
        return tomllib.load(f)

def get_db_path(config: dict | None = None) -> Path:
    if config is None:
        config = load_config()
    return get_plugin_root() / config["db_path"]

def read_hook_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, IOError):
        return {}

def normalize_path(p: str) -> str:
    return Path(p).resolve().as_posix().rstrip('/')

def resolve_project(cwd: str, project_map: dict) -> str | None:
    from _path_resolver import resolve_cwd
    resolved = resolve_cwd(cwd)
    cwd_norm = normalize_path(resolved)
    best_match, best_len = None, 0
    for path_key, project_id in project_map.items():
        key_norm = normalize_path(path_key)
        if (cwd_norm.casefold() == key_norm.casefold() or
            cwd_norm.casefold().startswith(key_norm.casefold() + '/')):
            if len(key_norm) > best_len:
                best_match, best_len = project_id, len(key_norm)
    return best_match

def compute_recency(days: float, hit_count: int,
                    half_life: float = 14.0, floor: float = 0.01,
                    boost_factor: float = 0.3) -> float:
    decay = 0.5 ** (days / half_life)
    boost = 1.0 + boost_factor * math.log1p(hit_count)
    return max(decay * boost, floor)

def get_logger(name: str):
    import logging
    log_path = get_plugin_root() / "logs" / f"{name}.log"
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
