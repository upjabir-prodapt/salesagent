"""Vertex Vector Search queries against the published product catalog index."""

from __future__ import annotations

import json
from typing import Any

import google.cloud.aiplatform as aiplatform
from vertexai.language_models import TextEmbeddingModel

from ...core.config import settings
from ...core.logging_config import logger
from ...dependencies.service_dependencies import get_storage_client

_embedding_model: TextEmbeddingModel | None = None
_index_endpoint: aiplatform.MatchingEngineIndexEndpoint | None = None
_catalog_chunks: dict[str, str] | None = None

aiplatform.init(
    project=settings.GOOGLE_CLOUD_PROJECT,
    location=settings.GOOGLE_CLOUD_LOCATION,
)


def _get_embedding_model() -> TextEmbeddingModel:
    global _embedding_model
    if _embedding_model is None:
        _embedding_model = TextEmbeddingModel.from_pretrained(
            settings.VECTOR_SEARCH_EMBEDDING_MODEL
        )
    return _embedding_model


def get_query_embedding(query: str) -> list[float]:
    """Convert the search query into an embedding vector."""
    embeddings = _get_embedding_model().get_embeddings([query])
    return embeddings[0].values


def _get_index_endpoint() -> aiplatform.MatchingEngineIndexEndpoint:
    global _index_endpoint
    if _index_endpoint is None:
        _index_endpoint = aiplatform.MatchingEngineIndexEndpoint(
            index_endpoint_name=settings.VECTOR_SEARCH_INDEX_ENDPOINT_ID,
        )
        if settings.VECTOR_SEARCH_PSC_IP:
            _index_endpoint.private_service_connect_ip_address = (
                settings.VECTOR_SEARCH_PSC_IP
            )
    return _index_endpoint


def _load_catalog_chunks() -> dict[str, str]:
    """Optional GCS map from vector id → catalog text snippet."""
    global _catalog_chunks
    if _catalog_chunks is not None:
        return _catalog_chunks

    _catalog_chunks = {}
    blob_path = settings.vector_search_catalog_chunks_blob

    try:
        storage_client = get_storage_client()
        blob = storage_client.bucket(settings.VECTOR_SEARCH_BUCKET).blob(blob_path)
        parsed = json.loads(blob.download_as_text())
        if (
            isinstance(parsed, dict)
            and "chunks" in parsed
            and isinstance(parsed["chunks"], list)
        ):
            _catalog_chunks = {
                str(item.get("id")): str(item.get("text", "")).strip()
                for item in parsed["chunks"]
                if isinstance(item, dict) and item.get("id")
            }
        elif isinstance(parsed, dict):
            _catalog_chunks = {
                str(k): str(v).strip() for k, v in parsed.items() if str(v).strip()
            }
    except Exception as exc:
        logger.warning("Failed to load catalog chunks map: %s", exc)

    return _catalog_chunks


def _format_neighbor(neighbor: Any, index: int, chunks: dict[str, str]) -> str:
    distance = getattr(neighbor, "distance", None)
    dist_str = f"{distance:.4f}" if isinstance(distance, int | float) else str(distance)
    match_info = f"Match {index + 1} - ID: {neighbor.id} (Confidence: {dist_str})"

    if hasattr(neighbor, "restricts") and neighbor.restricts:
        meta_str = ", ".join(
            f"{r.namespace}:{r.allow_list}" for r in neighbor.restricts
        )
        match_info += f" [Metadata: {meta_str}]"

    snippet = chunks.get(str(getattr(neighbor, "id", "")).strip(), "")
    if snippet:
        match_info += f"\nSnippet: {snippet[:900]}"

    return match_info


def colt_product_search(query: str) -> str:
    """Search the Colt Product Catalog using Vertex AI Vector Search."""
    try:
        query_vector = get_query_embedding(query)
        index_endpoint = _get_index_endpoint()

        response = index_endpoint.match(
            deployed_index_id=settings.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
            queries=[query_vector],
            num_neighbors=settings.VECTOR_SEARCH_NUM_NEIGHBORS,
        )

        chunks = _load_catalog_chunks()
        results: list[str] = []
        if response and len(response) > 0:
            for i, neighbor in enumerate(response[0]):
                results.append(_format_neighbor(neighbor, i, chunks))

        if not results:
            return (
                f"No matching products found in the Colt Catalog for query: '{query}'. "
                "This might indicate an empty index or strict search parameters."
            )

        output = (
            f"Top {len(results)} matches from the Colt Product Catalog for '{query}':\n"
            + "\n".join(results)
        )
        output += (
            "\n\nNote: Map these IDs to the COLT_DETAILS provided in your system "
            "instructions to identify the specific product."
        )
        return output
    except Exception as e:
        logger.exception("colt_product_search failed for query=%r", query)
        return f"Error performing product search: {str(e)}"
