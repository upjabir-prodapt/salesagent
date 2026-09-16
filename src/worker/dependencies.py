"""FastAPI service dependencies for the Worker role."""

from __future__ import annotations

from typing import Any

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


def _rate_limit_kwargs() -> dict:
    """The RESOURCE_EXHAUSTED-specific half of every step's policy.

    Shared by all four steps so a 429 is absorbed identically wherever it
    lands. RetryPolicy leaves these None by default, so this factory is
    the single place the production service opts into the larger
    rate-limit budget.
    """
    return {
        "rate_limit_max_attempts": settings.AGENT_RATE_LIMIT_RETRY_ATTEMPTS,
        "rate_limit_initial_delay": settings.AGENT_RATE_LIMIT_INITIAL_DELAY,
        "rate_limit_max_delay": settings.AGENT_RATE_LIMIT_MAX_DELAY,
        "initial_delay": settings.AGENT_RETRY_INITIAL_DELAY,
        "max_delay": settings.AGENT_RETRY_MAX_DELAY,
        "respect_retry_after": settings.AGENT_RETRY_RESPECT_RETRY_AFTER,
    }


def build_research_pipeline(*, cache_repo: Any | None = None) -> ResearchPipeline:
    """Construct the 4-step ResearchPipeline with production dependencies.

    Every step's RetryPolicy carries three things the defaults do not: the
    larger RATE_LIMIT budget, the exponential-backoff shape (previously
    hard-coded in RetryPolicy.__init__ and unreachable from config), and a
    hard per-step wall-clock ceiling so the four budgets provably fit
    inside the 1800s Cloud Tasks dispatch deadline.

    *cache_repo* overrides the search cache backend. Production leaves it
    None and gets RedisSearchCacheRepository; a local run with no
    Memorystore reachability (scripts/local_research_e2e.py) injects an
    in-process cache so the rest of the wiring -- models, retry policies,
    timeouts -- stays byte-identical to what the worker runs.
    """
    planner = QueryPlanner(
        retry=RetryPolicy(
            max_attempts=settings.PLANNER_RETRY_ATTEMPTS,
            max_elapsed=settings.PLANNER_STEP_BUDGET_SECONDS,
            **_rate_limit_kwargs(),
        )
    )
    searcher = SearchExecutor(
        get_genai_client(),
        cache_repo if cache_repo is not None else RedisSearchCacheRepository(),
        model=settings.SEARCH_AGENT_MODEL,
        qps=settings.SEARCH_QPS,
        qps_burst=settings.SEARCH_QPS_BURST,
        concurrency=settings.SEARCH_CONCURRENCY_LIMIT,
        # Per-query: this is the layer that actually meets the quota wall,
        # since the step fans out ~30 grounded searches. It also gets the
        # per-query timeout enforced for the first time (see _run_one).
        query_retry=RetryPolicy(
            max_attempts=settings.SEARCH_QUERY_RETRY_ATTEMPTS,
            timeout=settings.SEARCH_TIMEOUT_SECONDS,
            **_rate_limit_kwargs(),
        ),
        min_success_rate=settings.SEARCH_MIN_SUCCESS_RATE,
        step_retry=RetryPolicy(
            timeout=settings.SEARCH_STEP_TIMEOUT_SECONDS,
            max_elapsed=settings.SEARCH_STEP_BUDGET_SECONDS,
            **_rate_limit_kwargs(),
        ),
    )
    analyst = AlignmentAnalyst(
        retry=RetryPolicy(
            max_attempts=settings.ALIGNMENT_RETRY_ATTEMPTS,
            max_elapsed=settings.ALIGNMENT_STEP_BUDGET_SECONDS,
            **_rate_limit_kwargs(),
        )
    )
    compiler = ReportCompiler(
        retry=RetryPolicy(
            max_attempts=settings.COMPILER_RETRY_ATTEMPTS,
            timeout=settings.COMPILER_TIMEOUT_SECONDS,
            max_elapsed=settings.COMPILER_STEP_BUDGET_SECONDS,
            **_rate_limit_kwargs(),
        )
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
