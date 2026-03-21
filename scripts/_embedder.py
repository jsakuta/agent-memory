"""ruri-v3-30m ONNX embedding module."""

import numpy as np
from pathlib import Path


class Embedder:
    """ruri-v3-30m ONNX embedding generator.

    If model files are not found, available=False and embed() returns None.
    This allows FTS5-only mode when Vec is not configured.
    """

    def __init__(self, model_dir: str, max_length: int = 128):
        self.available = False
        self._session = None
        self._tokenizer = None
        self._max_length = max_length

        model_path = Path(model_dir)
        onnx_path = model_path / "model.onnx"
        tokenizer_path = model_path / "tokenizer.json"

        if not onnx_path.exists() or not tokenizer_path.exists():
            return

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            self._tokenizer.enable_padding(pad_id=0, length=max_length)
            self._tokenizer.enable_truncation(max_length=max_length)

            self._session = ort.InferenceSession(
                str(onnx_path),
                providers=["CPUExecutionProvider"],
            )
            self.available = True
        except Exception:
            self.available = False

    def embed(self, text: str) -> list[float] | None:
        """Generate 256-dim L2-normalized embedding for text.

        Returns None if embedder is not available or text is empty.
        """
        if not self.available or not text or not text.strip():
            return None

        try:
            encoded = self._tokenizer.encode(text)
            input_ids = np.array([encoded.ids], dtype=np.int64)
            attention_mask = np.array([encoded.attention_mask], dtype=np.int64)
            token_type_ids = np.zeros_like(input_ids)

            outputs = self._session.run(
                None,
                {
                    "input_ids": input_ids,
                    "attention_mask": attention_mask,
                    "token_type_ids": token_type_ids,
                },
            )

            # Mean pooling
            hidden = outputs[0]  # (1, seq_len, dim)
            mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
            pooled = (hidden * mask_expanded).sum(axis=1) / mask_expanded.sum(
                axis=1
            )

            # L2 normalize
            norm = np.linalg.norm(pooled, axis=1, keepdims=True)
            if norm[0][0] < 1e-12:
                return None
            embedding = (pooled / norm)[0]

            return embedding.tolist()
        except Exception:
            return None
