"""Vertex text embedding generation."""

from __future__ import annotations

from dataclasses import dataclass

import google.cloud.aiplatform as aiplatform
from vertexai.language_models import TextEmbeddingModel

from ...core.config import Settings
from ...core.logging_config import logger
from .chunking import TextChunk


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    embedding: list[float]
    text: str


def init_vertex(settings: Settings) -> None:
    aiplatform.init(
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def embed_chunks(settings: Settings, chunks: list[TextChunk]) -> list[VectorRecord]:
    init_vertex(settings)
    model = TextEmbeddingModel.from_pretrained(settings.VECTOR_SEARCH_EMBEDDING_MODEL)
    records: list[VectorRecord] = []
    batch_size = settings.VECTOR_SEARCH_EMBEDDING_BATCH_SIZE

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = model.get_embeddings([c.text for c in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            values = vector.values
            if len(values) != settings.VECTOR_SEARCH_EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"Chunk {chunk.chunk_id}: expected "
                    f"{settings.VECTOR_SEARCH_EMBEDDING_DIMENSIONS} dimensions, "
                    f"got {len(values)}"
                )
            records.append(
                VectorRecord(
                    chunk_id=chunk.chunk_id,
                    embedding=values,
                    text=chunk.text,
                )
            )
        done = min(start + batch_size, len(chunks))
        logger.info("Embedded %s/%s catalog chunks", done, len(chunks))

    return records
