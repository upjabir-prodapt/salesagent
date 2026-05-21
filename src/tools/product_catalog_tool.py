"""Product catalog vector search + chunk resolution for alignment agents."""

from __future__ import annotations

import json
from typing import Any

import google.cloud.aiplatform as aiplatform
from vertexai.language_models import TextEmbeddingModel

from ..core.config import settings
from ..core.logging_config import logger
from ..dependencies.service_dependencies import get_storage_client

# Initialize AI Platform
aiplatform.init(
    project=settings.GOOGLE_CLOUD_PROJECT, location=settings.GOOGLE_CLOUD_LOCATION
)


class ProductCatalogService:
    """Service for searching the product catalog using Vertex AI Vector Search."""

    def __init__(self):
        self._embedding_model: TextEmbeddingModel | None = None
        self._catalog_chunks: dict[str, str] | None = None
        self._index_endpoint: aiplatform.MatchingEngineIndexEndpoint | None = None

    def _get_embedding_model(self) -> TextEmbeddingModel:
        if self._embedding_model is None:
            self._embedding_model = TextEmbeddingModel.from_pretrained(
                settings.VECTOR_SEARCH_EMBEDDING_MODEL
            )
        return self._embedding_model

    def _get_index_endpoint(self) -> aiplatform.MatchingEngineIndexEndpoint:
        if self._index_endpoint is None:
            self._index_endpoint = aiplatform.MatchingEngineIndexEndpoint(
                settings.VECTOR_SEARCH_INDEX_ENDPOINT_ID
            )
        return self._index_endpoint

    def _load_catalog_chunks(self) -> dict[str, str]:
        """Load catalog chunk map from GCS once and cache in memory."""
        if self._catalog_chunks is not None:
            return self._catalog_chunks

        try:
            storage_client = get_storage_client()
            blob = storage_client.bucket(settings.VECTOR_SEARCH_BUCKET).blob(
                settings.VECTOR_SEARCH_CATALOG_CHUNKS_BLOB
            )
            if not blob.exists():
                logger.warning(
                    "Catalog chunk map not found: gs://%s/%s",
                    settings.VECTOR_SEARCH_BUCKET,
                    settings.VECTOR_SEARCH_CATALOG_CHUNKS_BLOB,
                )
                self._catalog_chunks = {}
                return self._catalog_chunks
            raw = blob.download_as_text()
            parsed = json.loads(raw)

            # Accept either {"id":"text"} map or {"chunks":[{"id":"...","text":"..."}]}.
            if (
                isinstance(parsed, dict)
                and "chunks" in parsed
                and isinstance(parsed["chunks"], list)
            ):
                self._catalog_chunks = {
                    str(item.get("id")): str(item.get("text", "")).strip()
                    for item in parsed["chunks"]
                    if isinstance(item, dict) and item.get("id")
                }
            elif isinstance(parsed, dict):
                self._catalog_chunks = {
                    str(k): str(v).strip() for k, v in parsed.items() if str(v).strip()
                }
            else:
                self._catalog_chunks = {}
        except Exception as exc:
            logger.warning("Failed to load catalog chunks map: %s", exc)
            self._catalog_chunks = {}
        return self._catalog_chunks

    def _resolve_neighbor_text(self, neighbor: Any, chunks: dict[str, str]) -> str:
        """Best-effort resolution from vector ID to product catalog text."""
        vector_id = str(getattr(neighbor, "id", "")).strip()
        return chunks.get(vector_id, "")

    def get_query_embedding(self, query: str) -> list[float]:
        """Converts the search query into a vector."""
        model = self._get_embedding_model()
        embeddings = model.get_embeddings([query])
        return embeddings[0].values

    def search(self, query: str) -> str:
        """Search the Colt Product Catalog using Vertex AI Vector Search."""
        try:
            index_endpoint = self._get_index_endpoint()
            query_vector = self.get_query_embedding(query)

            response = index_endpoint.find_neighbors(
                deployed_index_id=settings.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
                queries=[query_vector],
                num_neighbors=5,
            )

            chunk_map = self._load_catalog_chunks()
            results = []
            for neighbor in response[0]:
                vector_id = str(getattr(neighbor, "id", ""))
                distance = getattr(neighbor, "distance", None)
                resolved_text = self._resolve_neighbor_text(neighbor, chunk_map)
                if resolved_text:
                    results.append(
                        f"Catalog match (ID: {vector_id}, Distance: {distance})\n"
                        f"Snippet: {resolved_text[:900]}"
                    )
                else:
                    results.append(
                        f"Catalog match (ID: {vector_id}, Distance: {distance})"
                    )

            if not results:
                return "No matching products found in the Colt Catalog for this query."

            return "\n\n".join(results)
        except Exception as e:
            return f"Error performing product search: {str(e)}"


# Singleton instance
_catalog_service = ProductCatalogService()


def colt_product_search(query: str) -> str:
    """
    ADK-compatible tool to search the Colt Product Catalog using Vertex AI Vector Search.

    Args:
        query: The customer need or keyword to search for (e.g., 'high bandwidth cloud connect')
    """
    return _catalog_service.search(query)
