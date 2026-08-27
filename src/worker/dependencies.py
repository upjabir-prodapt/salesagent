"""FastAPI service dependencies for the Worker role."""

from __future__ import annotations

from src.shared.config import settings
from src.shared.repositories.clients import get_genai_client
from src.shared.repositories.redis_repository import RedisSearchCacheRepository
from src.worker.agents.alignment import AlignmentAnalyst
from src.worker.agents.base import RetryPolicy
from src.worker.agents.compiler import ReportCompiler
from src.worker.agents.planner import QueryPlanner
from src.worker.agents.search import SearchExecutor
from src.worker.pipeline import ResearchPipeline

from ..shared.repositories.bigquery_repository import BigQueryRepository
from ..shared.repositories.gcs_repository import GCSRepository
from .api.handlers import ResearchTaskHandler
from .services.artifacts import ResearchArtifactService
from .services.finalization_service import ResearchFinalizationService
from .services.job_runner import ResearchJobRunner

_bq_repo: BigQueryRepository | None = None
_gcs_repo: GCSRepository | None = None
_job_runner: ResearchJobRunner | None = None


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


def build_research_pipeline() -> ResearchPipeline:
    """Construct the 4-step ResearchPipeline with production dependencies."""
    planner = QueryPlanner(
        retry=RetryPolicy(max_attempts=settings.PLANNER_RETRY_ATTEMPTS)
    )
    searcher = SearchExecutor(
        get_genai_client(),
        RedisSearchCacheRepository(),
        model=settings.SEARCH_AGENT_MODEL,
        qps=settings.SEARCH_QPS,
        qps_burst=settings.SEARCH_QPS_BURST,
        concurrency=settings.SEARCH_CONCURRENCY_LIMIT,
        query_retry=RetryPolicy(
            max_attempts=settings.SEARCH_QUERY_RETRY_ATTEMPTS,
            timeout=settings.SEARCH_TIMEOUT_SECONDS,
        ),
        min_success_rate=settings.SEARCH_MIN_SUCCESS_RATE,
    )
    analyst = AlignmentAnalyst(
        retry=RetryPolicy(max_attempts=settings.ALIGNMENT_RETRY_ATTEMPTS)
    )
    compiler = ReportCompiler(
        retry=RetryPolicy(max_attempts=settings.COMPILER_RETRY_ATTEMPTS)
    )
    return ResearchPipeline(planner, searcher, analyst, compiler)


def get_research_job_runner() -> ResearchJobRunner:
    """Get shared ResearchJobRunner instance for worker."""
    global _job_runner
    if _job_runner is None:
        bq_repo = get_bigquery_repository()
        gcs_repo = get_gcs_repository()
        _job_runner = ResearchJobRunner(
            pipeline=build_research_pipeline(),
            bigquery_repository=bq_repo,
            artifact_service=ResearchArtifactService(bq_repo, gcs_repo),
            finalization_service=ResearchFinalizationService(bq_repo, gcs_repo),
        )
    return _job_runner


def get_research_task_handler() -> ResearchTaskHandler:
    """Get research task handler instance."""
    return ResearchTaskHandler(
        job_runner=get_research_job_runner(),
        bigquery_repository=get_bigquery_repository(),
    )
