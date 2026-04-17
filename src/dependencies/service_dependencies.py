"""Service Dependencies - Dependency Injection for Services"""

from functools import lru_cache

from google.cloud import bigquery, storage

from ..agents.research_service import ResearchService

# from google.cloud.firestore import AsyncClient
from ..core.config import settings
from ..repositories.bigquery_repository import BigQueryRepository
from ..repositories.firestore_repository import FirestoreRepository
from ..repositories.gcs_repository import GCSRepository


@lru_cache
def get_bigquery_client() -> bigquery.Client:
    """Get BigQuery client singleton"""
    return bigquery.Client(project=settings.GOOGLE_CLOUD_PROJECT)


@lru_cache
def get_storage_client() -> storage.Client:
    """Get Storage client singleton"""
    return storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)


# @lru_cache
# def get_firestore_client() -> AsyncClient:
#     """Get Firestore client singleton"""
#     return AsyncClient(project=settings.GOOGLE_CLOUD_PROJECT)


def get_bigquery_repository() -> BigQueryRepository:
    """Get BigQuery repository instance"""
    client = get_bigquery_client()
    return BigQueryRepository(client=client)


def get_gcs_repository() -> GCSRepository:
    """Get GCS repository instance"""
    client = get_storage_client()
    return GCSRepository(client=client)


def get_firestore_repository() -> FirestoreRepository:
    """Get Firestore repository instance"""
    # client = get_firestore_client()
    return FirestoreRepository(client=None)


def get_research_service() -> ResearchService:
    """Get Research service instance"""
    bigquery_repo = get_bigquery_repository()
    gcs_repo = get_gcs_repository()
    firestore_repo = get_firestore_repository()
    return ResearchService(
        bigquery_repository=bigquery_repo,
        gcs_repository=gcs_repo,
        firestore_repository=firestore_repo,
    )
