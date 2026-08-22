"""Service dependencies and shared GCP client pooling."""

import threading

from google import genai
from google.cloud import bigquery, firestore, storage  # type: ignore

from ..core.config import settings

_bq_client: bigquery.Client | None = None
_firestore_client: firestore.Client | None = None
_storage_client: storage.Client | None = None
_genai_client: genai.Client | None = None
_lock = threading.Lock()


def get_bigquery_client() -> bigquery.Client:
    """Get shared BigQuery client singleton."""
    global _bq_client
    with _lock:
        if _bq_client is None:
            _bq_client = bigquery.Client(project=settings.GOOGLE_CLOUD_PROJECT)
    return _bq_client


def get_firestore_client() -> firestore.Client:
    """Get shared Firestore client singleton."""
    global _firestore_client
    with _lock:
        if _firestore_client is None:
            _firestore_client = firestore.Client(
                project=settings.GOOGLE_CLOUD_PROJECT,
                database=settings.FIRESTORE_DATABASE,
            )
    return _firestore_client


def get_storage_client() -> storage.Client:
    """Get shared GCS client singleton."""
    global _storage_client
    with _lock:
        if _storage_client is None:
            _storage_client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
    return _storage_client


def get_genai_client() -> genai.Client:
    """Get shared Google Gen AI client singleton."""
    global _genai_client
    with _lock:
        if _genai_client is None:
            _genai_client = genai.Client()
    return _genai_client


def get_bigquery_repository():
    """Get BigQuery repository instance."""
    from ..repositories.bigquery_repository import BigQueryRepository

    return BigQueryRepository(client=get_bigquery_client())


def get_search_cache_repository():
    """Get Firestore search cache repository instance."""
    from ..repositories.firestore_repository import FirestoreSearchCacheRepository

    return FirestoreSearchCacheRepository(client=get_firestore_client())


def get_gcs_repository():
    """Get GCS repository instance."""
    from ..repositories.gcs_repository import GCSRepository

    return GCSRepository(client=get_storage_client())


def get_research_service():
    """Get Research service instance."""
    from ..services.research import ResearchService

    return ResearchService(
        bigquery_repository=get_bigquery_repository(),
        gcs_repository=get_gcs_repository(),
    )


def get_catalog_service():
    """Get Catalog vector index service instance."""
    from ..services.catalog import CatalogService

    return CatalogService()
