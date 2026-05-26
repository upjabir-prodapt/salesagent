"""Tests for embedding similarity helpers."""

import numpy as np

from src.services.research.agent.sales.utils.embedding_similarity import (
    max_cosine_similarity,
)


def test_max_cosine_similarity_identical():
    vec = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
    sim = max_cosine_similarity(vec[0], vec)
    assert sim >= 0.99


def test_max_cosine_similarity_empty_corpus():
    vec = np.array([1.0, 0.0], dtype=np.float32)
    assert max_cosine_similarity(vec, np.zeros((0, 2))) == 0.0
