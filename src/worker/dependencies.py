"""FastAPI service dependencies for the Worker role."""

from __future__ import annotations

from ..shared.repositories.bigquery_repository import BigQueryRepository
from ..shared.repositories.gcs_repository import GCSRepository
from .api.handlers import ResearchTaskHandler
from .services.pipeline_service import ResearchPipelineService

_bq_repo: BigQueryRepository | None = None
_gcs_repo: GCSRepository | None = None
_pipeline_svc: ResearchPipelineService | None = None


def get_bigquery_repository() -> BigQueryRepository:
    """Get shared BigQuery repository instance for worker."""
    global _bq_repo
    if _bq_repo is None:
        _bq_repo = BigQueryRepository()
    return _bq_repo


def get_gcs_repository() -> GCSRepository:
    """Get shared GCS repository instance for worker."""
    global _gcs_repo
    if _gcs_repo is None:
        _gcs_repo = GCSRepository()
    return _gcs_repo


def get_research_pipeline_service() -> ResearchPipelineService:
    """Get shared ResearchPipelineService instance for worker."""
    global _pipeline_svc
    if _pipeline_svc is None:
        _pipeline_svc = ResearchPipelineService(
            bigquery_repository=get_bigquery_repository(),
            gcs_repository=get_gcs_repository(),
        )
    return _pipeline_svc


def get_research_task_handler() -> ResearchTaskHandler:
    """Get research task handler instance."""
    return ResearchTaskHandler(
        pipeline_service=get_research_pipeline_service(),
        bigquery_repository=get_bigquery_repository(),
    )
