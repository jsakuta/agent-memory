import json, os, sys, math, tomllib, platform
from pathlib import Path
from collections import namedtuple

# Python 3.13 WMI bypass: platform.system() calls _wmi_query() which hangs
# when WMI service is slow (30s+). Pre-cache uname to skip WMI entirely.
if sys.platform == "win32" and not hasattr(platform, '_uname_cache'):
    _uname_nt = namedtuple('uname_result', ['system', 'node', 'release', 'version', 'machine'])
    platform._uname_cache = _uname_nt('Windows', '', '', '', '')

def get_plugin_root() -> Path:
    return Path(__file__).resolve().parent.parent

def get_data_root() -> Path:
    """Runtime data の root。CLAUDE_PLUGIN_DATA > PLUGIN_ROOT の順で解決。"""
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        return Path(plugin_data)
    return get_plugin_root()

def load_config() -> dict:
    # 1. デフォルト設定（PLUGIN_ROOT 同梱）
    default_path = get_plugin_root() / "config" / "settings.default.toml"
    if not default_path.exists():
        # フォールバック: 旧名 settings.toml
        default_path = get_plugin_root() / "config" / "settings.toml"
    with open(default_path, "rb") as f:
        config = tomllib.load(f)
    # 2. ユーザー設定（上書きマージ）
    #    PLUGIN_DATA が設定済み → PLUGIN_DATA/settings.toml
    #    未設定（ローカル開発）→ PLUGIN_ROOT/config/settings.toml
    plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA")
    if plugin_data:
        user_path = Path(plugin_data) / "settings.toml"
    else:
        user_path = get_plugin_root() / "config" / "settings.toml"
    if user_path.exists() and user_path.resolve() != default_path.resolve():
        with open(user_path, "rb") as f:
            user_config = tomllib.load(f)
        config.update(user_config)
    return config

def get_db_path(config: dict | None = None) -> Path:
    if config is None:
        config = load_config()
    return get_data_root() / config["db_path"]

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
    log_dir = get_data_root() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{name}.log"
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
