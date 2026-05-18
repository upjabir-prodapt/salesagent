"""Service dependencies and shared GCP client pooling."""

from google import genai
from google.cloud import bigquery, storage

from ..core.clients import client_pool
from ..repositories.bigquery_repository import BigQueryRepository
from ..repositories.gcs_repository import GCSRepository


def get_bigquery_client() -> bigquery.Client:
    """Get shared BigQuery client singleton."""
    return client_pool.get_bq_client()


def get_storage_client() -> storage.Client:
    """Get shared GCS client singleton."""
    return client_pool.get_storage_client()


def get_genai_client() -> genai.Client:
    """Get shared Google Gen AI client singleton."""
    return client_pool.get_genai_client()


def get_bigquery_repository() -> BigQueryRepository:
    """Get BigQuery repository instance."""
    return BigQueryRepository(client=get_bigquery_client())


def get_gcs_repository() -> GCSRepository:
    """Get GCS repository instance."""
    return GCSRepository(client=get_storage_client())


def get_research_service():
    """Get Research service instance."""
    from ..agents.research_service import ResearchService

    return ResearchService(
        bigquery_repository=get_bigquery_repository(),
        gcs_repository=get_gcs_repository(),
    )
