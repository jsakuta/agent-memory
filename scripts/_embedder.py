"""ruri-v3 ONNX embedding module.

Supports ruri-v3-130m INT8 (512dim, 8192 tokens) and legacy ruri-v3-30m (256dim).
If model files are not found, available=False and embed() returns None,
allowing FTS5-only mode when Vec is not configured.
"""

import numpy as np
from pathlib import Path


class Embedder:
    def __init__(self, model_dir: str, max_length: int = 8192):
        self.available = False
        self._session = None
        self._tokenizer = None
        self._max_length = max_length
        self._input_names: list[str] = []
        self._output_names: list[str] = []

        model_path = Path(model_dir)
        # INT8 preferred, F32 fallback
        onnx_path = model_path / "model_int8.onnx"
        if not onnx_path.exists():
            onnx_path = model_path / "model.onnx"
        tokenizer_path = model_path / "tokenizer.json"

        if not onnx_path.exists() or not tokenizer_path.exists():
            return

        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
            # Truncation only — NO fixed padding (catastrophic slowdown with 8192)
            self._tokenizer.enable_truncation(max_length=max_length)
            self._tokenizer.no_padding()

            self._session = ort.InferenceSession(
                str(onnx_path),
                providers=["CPUExecutionProvider"],
            )
            self._input_names = [inp.name for inp in self._session.get_inputs()]
            self._output_names = [out.name for out in self._session.get_outputs()]
            self.available = True
        except Exception:
            self.available = False

    def embed(self, text: str, prefix: str = "") -> list[float] | None:
        """Generate L2-normalized embedding for text.

        Args:
            text: Input text.
            prefix: ruri-v3 prefix ("検索クエリ: " or "検索文書: " or "").
        """
        if not self.available or not text or not text.strip():
            return None

        try:
            input_text = prefix + text if prefix else text
            encoded = self._tokenizer.encode(input_text)
            input_ids = np.array([encoded.ids], dtype=np.int64)
            attention_mask = np.array([encoded.attention_mask], dtype=np.int64)

            # Build feed dict dynamically (ModernBERT has no token_type_ids)
            feed = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros_like(input_ids)

            outputs = self._session.run(None, feed)

            # Use sentence_embedding if available (ruri-v3-130m), else mean pooling
            if "sentence_embedding" in self._output_names:
                idx = self._output_names.index("sentence_embedding")
                pooled = outputs[idx]  # (1, dim)
            else:
                # Mean pooling fallback (ruri-v3-30m)
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
