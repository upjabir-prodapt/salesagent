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
from src.worker.services.task_attempt import (
    JobPhase,
    TaskAttempt,
    should_redispatch,
)

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
        attempt: TaskAttempt | None = None,
    ) -> None:
        """Execute the full pipeline for one job, from PROCESSING to terminal state.

        *attempt* is the Cloud Tasks delivery context, when this run came
        from the queue. It decides whether a failure is left retryable
        (status untouched, so the next delivery re-runs the job) or
        settled as FAILED. None -- the local in-process dev path, where no
        queue exists to re-deliver anything -- always settles as FAILED.
        """
        logger.info(
            f"[Pipeline] Starting research job job_id={job_id} company={company_name!r}"
            + (f" ({attempt.label})" if attempt is not None else "")
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
        except Exception as error:
            # Pipeline phase: nothing irreversible has been written yet, so
            # a transient failure here is safe to re-deliver.
            self._handle_failure(
                error, job_id, span, phase=JobPhase.PIPELINE, attempt=attempt
            )
            raise

        try:
            await self._finalize_success(job_id, result, metadata, start_time, span)
        except Exception as error:
            # Finalization phase: the report is already in GCS and the
            # side-ops may have partially applied (cost attribution rows,
            # PDF, telemetry). Re-running the job would duplicate them, so
            # this is terminal regardless of the error kind.
            self._handle_failure(
                error, job_id, span, phase=JobPhase.FINALIZATION, attempt=attempt
            )
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

    def _handle_failure(
        self,
        error: Exception,
        job_id: str,
        span: Span | None,
        *,
        phase: JobPhase = JobPhase.PIPELINE,
        attempt: TaskAttempt | None = None,
    ) -> None:
        """Record the failure, terminally or as awaiting another delivery."""
        error_msg = str(error)
        if isinstance(error, ExceptionGroup):
            error_msg = "Parallel execution collapsed (likely Quota/QPM limit reached)"

        if span is not None:
            span.record_exception(error)

        redispatch = should_redispatch(error, attempt, phase)
        if redispatch and attempt is not None:
            # Leave the status alone -- it is still PROCESSING, and that is
            # the truth: another delivery is queued. Writing FAILED here is
            # precisely what used to kill the queue-level retry, because
            # the next delivery read it back and no-opped.
            if span is not None:
                span.set_attribute("research.status", "retrying")
            logger.warning(
                f"[Pipeline] Transient failure on job_id={job_id} "
                f"({attempt.label}); leaving job retryable for Cloud Tasks "
                f"re-delivery: {error}"
            )
            self._bigquery_repo.update_status(
                job_id,
                None,
                current_step=(f"Retrying after a transient error ({attempt.label})"),
                metadata_update={
                    "last_transient_error": error_msg[:1000],
                    "delivery_attempts": attempt.attempt_number,
                },
            )
            return

        if span is not None:
            span.set_attribute("research.status", "failed")
        logger.error(
            f"[Pipeline] Error processing research for job_id={job_id}: {error}"
        )
        failure_metadata: dict = {"raw_error": str(error)[:1000], "failed_phase": phase}
        if attempt is not None:
            failure_metadata["delivery_attempts"] = attempt.attempt_number
        self._bigquery_repo.update_status(
            job_id,
            "FAILED",
            error=error_msg,
            metadata_update=failure_metadata,
        )


__all__ = ["ResearchJobRunner"]
