"""GCS path conventions for versioned catalog releases."""

from __future__ import annotations

from datetime import UTC, datetime

from ...core.config import Settings

SOURCE_FILENAME = "ColtProductCatalog.pdf"
VECTORS_FILENAME = "data.json"
CHUNKS_FILENAME = "chunks.json"
MANIFEST_FILENAME = "manifest.json"


class CatalogPaths:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def catalog_root(self) -> str:
        return self._settings.VECTOR_SEARCH_CATALOG_ROOT.strip("/")

    @property
    def bucket(self) -> str:
        return self._settings.VECTOR_SEARCH_BUCKET

    def release_prefix(self, version_id: str) -> str:
        return f"{self.catalog_root}/releases/{version_id}"

    def release_source_blob(self, version_id: str) -> str:
        return f"{self.release_prefix(version_id)}/source/{SOURCE_FILENAME}"

    def release_chunks_blob(self, version_id: str) -> str:
        return f"{self.release_prefix(version_id)}/chunks/{CHUNKS_FILENAME}"

    def release_vectors_blob(self, version_id: str) -> str:
        return f"{self.release_prefix(version_id)}/embeddings/{VECTORS_FILENAME}"

    def release_embeddings_dir(self, version_id: str) -> str:
        return f"{self.release_prefix(version_id)}/embeddings"

    def manifest_blob(self) -> str:
        return f"{self.catalog_root}/{MANIFEST_FILENAME}"

    def current_chunks_blob(self) -> str:
        return self._settings.vector_search_catalog_chunks_blob

    def gcs_uri(self, blob_path: str) -> str:
        return f"gs://{self.bucket}/{blob_path}"

    def embeddings_delta_uri(self, version_id: str) -> str:
        return self.gcs_uri(self.release_embeddings_dir(version_id))

    def build_manifest(
        self,
        *,
        active_version: str,
        vector_count: int,
        source_sha256: str,
    ) -> dict:
        now = datetime.now(UTC).isoformat()
        cfg = self._settings
        release = {
            "version_id": active_version,
            "created_at": now,
            "source_pdf": self.release_source_blob(active_version),
            "chunks": self.release_chunks_blob(active_version),
            "embeddings_dir": self.release_embeddings_dir(active_version),
            "embeddings_file": self.release_vectors_blob(active_version),
            "vector_count": vector_count,
            "source_sha256": source_sha256,
            "embedding_model": cfg.VECTOR_SEARCH_EMBEDDING_MODEL,
            "chunk_size": cfg.VECTOR_SEARCH_CHUNK_SIZE,
            "chunk_overlap": cfg.VECTOR_SEARCH_CHUNK_OVERLAP,
            "index_id": cfg.VECTOR_SEARCH_INDEX_ID,
            "index_endpoint_id": cfg.VECTOR_SEARCH_INDEX_ENDPOINT_ID,
            "deployed_index_id": cfg.VECTOR_SEARCH_DEPLOYED_INDEX_ID,
        }
        return {
            "catalog_id": self.catalog_root,
            "active_version": active_version,
            "updated_at": now,
            "current": {"chunks": self.current_chunks_blob()},
            "releases": {active_version: release},
        }
