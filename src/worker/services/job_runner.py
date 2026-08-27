"""ResearchJobRunner: drives ResearchPipeline for one BigQuery-tracked job.

Replaces:
  - services/orchestrator.py (ResearchJobOrchestrator + 4 pass-through
    adapter classes + ResearchApplicationService/ResearchJobCommand)
  - runtime/runner.py (ADK multi-agent Runner lifecycle wrapper)
  - services/pipeline_service.py (thin construction wrapper)

Because ReportCompiler.validate() already enforces PASSED status inside
its own retry loop (raising AgentError up through ResearchPipeline.run()
if retries are exhausted), a successful pipeline.run() call always
produces a validated report -- there is no separate "validation failed
but continue anyway" branch to orchestrate here, unlike the old
_handle_validation_failure path.
"""

from __future__ import annotations

import time

from opentelemetry.trace import Span

from src.shared.config import settings
from src.shared.logging_config import logger
from src.shared.repositories.bigquery_repository import BigQueryRepository
from src.worker.agents.models import PipelineResult, ResearchRequest
from src.worker.observers import (
    CompositeObserver,
    Observer,
    ProgressObserver,
    TracingObserver,
)
from src.worker.pipeline import ResearchPipeline
from src.worker.services.artifacts import ResearchArtifactService
from src.worker.services.finalization_service import ResearchFinalizationService
from src.worker.services.formatting import clean_markdown_report
from src.worker.services.metrics import calculate_metrics, reconcile_cost
from src.worker.services.status import build_completion_metadata

_TOTAL_STEPS = 4  # QueryPlanner, SearchExecutor, AlignmentAnalyst, ReportCompiler


class ResearchJobRunner:
    """Coordinates ResearchPipeline execution, artifacts, and finalization."""

    def __init__(
        self,
        pipeline: ResearchPipeline,
        bigquery_repository: BigQueryRepository,
        artifact_service: ResearchArtifactService,
        finalization_service: ResearchFinalizationService,
    ) -> None:
        self._pipeline = pipeline
        self._bigquery_repo = bigquery_repository
        self._artifacts = artifact_service
        self._finalization = finalization_service

    async def run(
        self,
        job_id: str,
        company_name: str,
        metadata: dict | None = None,
        *,
        span: Span | None = None,
    ) -> None:
        """Execute the full pipeline for one job, from PROCESSING to terminal state."""
        logger.info(
            f"[Pipeline] Starting research job job_id={job_id} company={company_name!r}"
        )
        self._bigquery_repo.update_status(
            job_id,
            "PROCESSING",
            progress=settings.RESEARCH_INIT_PROGRESS,
            current_step=settings.RESEARCH_INIT_STEP_LABEL,
        )

        start_time = time.monotonic()
        try:
            result = await self._run_pipeline(job_id, company_name, span=span)
            await self._finalize_success(job_id, result, metadata, start_time, span)
        except Exception as error:
            self._handle_failure(error, job_id, span)
            raise

    async def _run_pipeline(
        self, job_id: str, company_name: str, *, span: Span | None
    ) -> PipelineResult:
        observer: Observer = CompositeObserver(
            [
                ProgressObserver(
                    job_id, self._bigquery_repo.update_status, _TOTAL_STEPS
                ),
                TracingObserver(),
            ]
        )
        request = ResearchRequest(job_id=job_id, company=company_name)
        return await self._pipeline.run(request, observer)

    async def _finalize_success(
        self,
        job_id: str,
        result: PipelineResult,
        metadata: dict | None,
        start_time: float,
        span: Span | None,
    ) -> None:
        final_report = clean_markdown_report(result.report.markdown)
        session_state = result.to_legacy_state()

        latency = round(time.monotonic() - start_time, 2)
        metrics = calculate_metrics(session_state, latency)
        if span is not None:
            span.set_attribute("research.latency_seconds", latency)
            if metrics["total_tokens"]:
                span.set_attribute(
                    "research.total_tokens", int(metrics["total_tokens"])
                )
            if metrics["cost_usd"] is not None:
                span.set_attribute("research.cost_usd", float(metrics["cost_usd"]))

        reconciliation = reconcile_cost(session_state, metrics)
        md_uri = self._artifacts.upload_artifacts(job_id, final_report, session_state)

        try:
            await self._artifacts.upload_agent_artifacts(job_id, session_state)
        except Exception as artifact_error:
            logger.warning(
                f"[Pipeline] Per-agent artifact upload failed job_id={job_id}: "
                f"{artifact_error}"
            )

        side_op_failures, pdf_available = await self._finalization.finalize(
            job_id, final_report, session_state, metrics, metadata=metadata
        )
        logger.info(
            f"[Pipeline] Finalization completed job_id={job_id} pdf_available={pdf_available}"
        )

        completion_metadata = build_completion_metadata(
            latency=latency,
            metrics=metrics,
            pdf_available=pdf_available,
            side_op_failures=side_op_failures,
            reconciliation=reconciliation,
        )
        self._bigquery_repo.update_status(
            job_id,
            "COMPLETED",
            gcs_uri=md_uri,
            progress=100,
            current_step="Completed",
            metadata_update=completion_metadata,
        )
        logger.info(f"[Pipeline] Research completed successfully for job {job_id}")
        if span is not None:
            span.set_attribute("research.status", "completed")

    def _handle_failure(self, error: Exception, job_id: str, span: Span | None) -> None:
        """Mark the job failed with normalized error context."""
        error_msg = str(error)
        if isinstance(error, ExceptionGroup):
            error_msg = "Parallel execution collapsed (likely Quota/QPM limit reached)"

        if span is not None:
            span.record_exception(error)
            span.set_attribute("research.status", "failed")

        logger.error(
            f"[Pipeline] Error processing research for job_id={job_id}: {error}"
        )
        self._bigquery_repo.update_status(
            job_id,
            "FAILED",
            error=error_msg,
            metadata_update={"raw_error": str(error)[:1000]},
        )


__all__ = ["ResearchJobRunner"]
