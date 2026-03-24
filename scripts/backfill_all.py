"""初回セットアップ用: モデル確保 → 既存セッション一括取込 → Vec埋め込み生成。
setup.mjs から fire-and-forget で起動される。
完了フラグ (data/backfill_complete) で2回目以降はスキップ。
"""
import subprocess
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import get_data_root, get_plugin_root, get_logger

MODEL_FILES = [
    {
        "url": "https://huggingface.co/sirasagi62/ruri-v3-130m-ONNX/resolve/main/onnx/model_int8.onnx",
        "name": "model_int8.onnx",
    },
    {
        "url": "https://huggingface.co/cl-nagoya/ruri-v3-130m/resolve/main/tokenizer.json",
        "name": "tokenizer.json",
    },
]


def ensure_model(logger) -> bool:
    """ONNXモデルを確保する。成功時True、失敗時False（FTS5のみで動作可能）。"""
    data_root = get_data_root()
    plugin_root = get_plugin_root()
    model_dir = data_root / "models" / "ruri-v3-130m"
    required = [f["name"] for f in MODEL_FILES]

    # Already present?
    if all((model_dir / name).exists() for name in required):
        return True

    # Try copy from plugin root (local dev / git checkout)
    src_dir = plugin_root / "models" / "ruri-v3-130m"
    if (src_dir / "model_int8.onnx").exists():
        model_dir.mkdir(parents=True, exist_ok=True)
        for name in required:
            src = src_dir / name
            if src.exists():
                shutil.copy2(str(src), str(model_dir / name))
        logger.info("ONNX model copied from plugin root")

    # Verify all files; download any missing
    missing = [f for f in MODEL_FILES if not (model_dir / f["name"]).exists()]
    if not missing:
        return True

    model_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading {len(missing)} model file(s)...")
    for f in missing:
        dst = model_dir / f["name"]
        try:
            subprocess.run(
                ["curl", "-fSL", "--retry", "3", "-o", str(dst), f["url"]],
                check=True, capture_output=True, timeout=600,
            )
        except Exception as e:
            logger.error(f"Download failed for {f['name']}: {e}")
            # Remove partial file
            dst.unlink(missing_ok=True)
            return False

    logger.info("Model download complete")
    return True


def main():
    logger = get_logger("backfill_all")
    flag = get_data_root() / "data" / "backfill_complete"

    if flag.exists():
        logger.info("Initial backfill already completed, skipping")
        return

    # Phase 0: Ensure ONNX model (for vec embeddings)
    model_ok = ensure_model(logger)
    if not model_ok:
        logger.warning("Model not available — vec embeddings will be skipped")

    # Phase 1: 既存JSONL → SQLite + FTS5
    logger.info("Phase 1: importing existing sessions...")
    try:
        from backfill_external import main as run_external
        run_external()
    except Exception as e:
        logger.error(f"backfill_external failed: {e}")

    # Phase 2: Vec embeddings (only if model available)
    if model_ok:
        logger.info("Phase 2: generating vec embeddings...")
        try:
            from backfill_vec import backfill as run_vec
            run_vec()
        except Exception as e:
            logger.error(f"backfill_vec failed: {e}")
    else:
        logger.info("Phase 2: skipped (no model)")

    # Mark complete
    try:
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("done")
        logger.info("Initial backfill complete")
    except OSError:
        pass


if __name__ == "__main__":
    main()
