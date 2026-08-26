"""
ONNX sentence-transformer semantic similarity for evaluation (Section B M5).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from src.shared.config import settings

from .verification import claims_from_answer

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_embedder: OnnxSentenceEmbedder | None = None


class OnnxSentenceEmbedder:
    """Lazy singleton: load ONNX model once per process."""

    def __init__(self, onnx_path: str) -> None:
        self._onnx_path = onnx_path
        self._session = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort
        from transformers import AutoTokenizer

        local_model_dir = settings.eval_embedding_onnx_path.parent.parent
        tokenizer_source = (
            str(local_model_dir)
            if (local_model_dir / "tokenizer_config.json").is_file()
            else settings.EVAL_EMBEDDING_MODEL
        )
        self._tokenizer = AutoTokenizer.from_pretrained(tokenizer_source)
        self._session = ort.InferenceSession(
            self._onnx_path,
            providers=["CPUExecutionProvider"],
        )

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        self._ensure_loaded()
        assert self._tokenizer is not None and self._session is not None

        encoded = self._tokenizer(
            texts,
            padding=True,
            truncation=True,
            max_length=256,
            return_tensors="np",
        )
        inputs = {
            "input_ids": encoded["input_ids"].astype(np.int64),
            "attention_mask": encoded["attention_mask"].astype(np.int64),
        }
        if "token_type_ids" in encoded:
            inputs["token_type_ids"] = encoded["token_type_ids"].astype(np.int64)

        outputs = self._session.run(None, inputs)
        embeddings = outputs[0]
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return (embeddings / norms).astype(np.float32)


def get_embedder() -> OnnxSentenceEmbedder | None:
    global _embedder
    if not settings.EVAL_EMBEDDING_ENABLED:
        return None
    if _embedder is None:
        path = settings.eval_embedding_onnx_path
        if not path.is_file():
            logger.warning(
                f"[Pipeline] ONNX embedding model not found at {path} — "
                f"semantic metrics disabled"
            )
            return None
        _embedder = OnnxSentenceEmbedder(str(path))
    return _embedder


def max_cosine_similarity(query_vec: np.ndarray, corpus_matrix: np.ndarray) -> float:
    if corpus_matrix.size == 0:
        return 0.0
    if query_vec.ndim == 1:
        query_vec = query_vec.reshape(1, -1)
    sims = np.dot(corpus_matrix, query_vec.T).flatten()
    return float(np.max(sims)) if sims.size else 0.0


def compute_semantic_groundedness(
    report: str,
    job_evidence: list[dict],
    *,
    sections: tuple[str, ...] = ("11.", "8."),
) -> float:
    """
    Fraction of checkable claims with max cosine sim >= threshold vs job_evidence.
    """
    embedder = get_embedder()
    if embedder is None or not job_evidence:
        return 0.0

    corpus_texts = [
        f"{e.get('title', '')} {e.get('snippet', '')}".strip()
        for e in job_evidence
        if (e.get("title") or e.get("snippet"))
    ]
    if not corpus_texts:
        return 0.0

    text_to_score = report
    for prefix in sections:
        idx = report.find(prefix)
        if idx >= 0:
            text_to_score = report[idx:]
            break

    claims = [
        c
        for c in claims_from_answer(text_to_score)
        if len(c) >= 20 and len(c.split()) >= 4
    ]
    if not claims:
        return 1.0

    try:
        corpus_vecs = embedder.encode(corpus_texts)
        claim_vecs = embedder.encode(claims)
    except Exception as e:
        logger.warning(f"[Pipeline] Embedding encode failed: {e}")
        return 0.0

    threshold = settings.EVAL_EMBEDDING_SIMILARITY_THRESHOLD
    grounded = 0
    for claim_vec in claim_vecs:
        sim = max_cosine_similarity(claim_vec, corpus_vecs)
        if sim >= threshold:
            grounded += 1

    return min(1.0, grounded / len(claims))
