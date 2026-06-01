"""Pipeline-style orchestration for background research jobs."""

from __future__ import annotations

import time

from opentelemetry.trace import Span

from ....core.config import settings
from ....core.logging_config import logger
from ....utils.guardrails import GuardrailViolation
from ..agents.sales.tools.evidence import aggregate_job_evidence
from ..agents.sales.tools.report_validation import ensure_report_validated
from ..infrastructure.ports import (
    AgentRunnerPort,
    ArtifactPort,
    FinalizationPort,
    StatusRepositoryPort,
)
from ..support.metrics import calculate_metrics, reconcile_cost
from ..support.status import (
    build_completion_metadata,
    build_failure_summary,
)


class ResearchJobOrchestrator:
    """Coordinates runner, artifacts, and finalization stages for one job."""

    def __init__(
        self,
        *,
        status_repository: StatusRepositoryPort,
        runner: AgentRunnerPort,
        artifacts: ArtifactPort,
        finalization: FinalizationPort,
    ) -> None:
        self._status_repository = status_repository
        self._runner = runner
        self._artifacts = artifacts
        self._finalization = finalization

    async def run(self, job_id: str, company_name: str, *, span: Span | None = None) -> None:
        """Execute the full background pipeline from run to completion state."""
        logger.info(
            f"[Pipeline] Starting research job job_id={job_id} company={company_name!r}"
        )
        self._status_repository.update_status(
            job_id,
            "PROCESSING",
            progress=settings.RESEARCH_INIT_PROGRESS,
            current_step=settings.RESEARCH_INIT_STEP_LABEL,
        )

        start_time = time.monotonic()
        try:
            final_report, session_state = await self._run_research_loop(
                job_id, company_name, start_time=start_time, span=span
            )
            if not final_report:
                return

            latency = round(time.monotonic() - start_time, 2)
            metrics = calculate_metrics(session_state, latency)
            if span is not None:
                span.set_attribute("research.latency_seconds", latency)
                if metrics["total_tokens"]:
                    span.set_attribute("research.total_tokens", int(metrics["total_tokens"]))
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
                job_id, final_report, session_state, metrics
            )

            logger.info(
                f"[Pipeline] Finalization completed job_id={job_id} "
                f"pdf_available={pdf_available}"
            )

            self._mark_completed(
                job_id,
                md_uri=md_uri,
                latency=latency,
                metrics=metrics,
                pdf_available=pdf_available,
                side_op_failures=side_op_failures,
                reconciliation=reconciliation,
            )
            if span is not None:
                span.set_attribute("research.status", "completed")
        except Exception as error:
            self._handle_failure(error, job_id, span)
            raise

    async def _run_research_loop(
        self,
        job_id: str,
        company_name: str,
        *,
        start_time: float,
        span: Span | None = None,
    ) -> tuple[str | None, dict]:
        """Execute sales agents, aggregate evidence, and enforce report validation gate."""
        logger.info(
            f"[Pipeline] Running research agents job_id={job_id} "
            f"company={company_name!r}"
        )
        final_report, session_state = await self._runner.run(job_id, company_name)

        session_state["job_evidence"] = aggregate_job_evidence(session_state)
        session_state["raw_search_cache"] = session_state["job_evidence"]
        if final_report:
            await ensure_report_validated(final_report, session_state)

        validation_status = session_state.get("report_validation_status")
        if validation_status != "PASSED":
            logger.warning(
                f"[Validation] Post-run report validation gate failed "
                f"job_id={job_id} status={validation_status!r}"
            )
            await self._handle_validation_failure(
                job_id,
                session_state,
                validation_status=validation_status,
                start_time=start_time,
                span=span,
            )
            return None, session_state

        return final_report, session_state

    async def _handle_validation_failure(
        self,
        job_id: str,
        session_state: dict,
        *,
        validation_status: str | None,
        start_time: float,
        span: Span | None,
    ) -> None:
        """Mark job failed, export telemetry/cost, and record OTEL attributes."""
        violations = session_state.get("report_validation_violations") or []
        guard_violations = [
            GuardrailViolation(rule=v.get("rule", "unknown"), detail=v.get("detail", ""))
            for v in violations
            if isinstance(v, dict)
        ]
        failure_summary = build_failure_summary(guard_violations)
        dominant = failure_summary.get("dominant_rule", "report_validation_failed")
        latency = round(time.monotonic() - start_time, 2)
        metrics = calculate_metrics(session_state, latency)
        reconciliation = reconcile_cost(session_state, metrics)

        side_op_failures: dict[str, str] = {}
        try:
            side_op_failures = await self._finalization.export_failure_telemetry(
                job_id, session_state, metrics
            )
        except Exception as export_error:
            logger.warning(
                f"[Pipeline] Validation-failure telemetry export failed for "
                f"job_id={job_id}: {export_error}"
            )
            side_op_failures["failure_telemetry_export"] = str(export_error)
            if span is not None:
                span.record_exception(export_error)

        logger.error(
            f"[Validation] Job {job_id} blocked: status={validation_status!r}, "
            f"dominant_rule={dominant}"
        )

        if span is not None:
            span.set_attribute("research.status", "failed")
            span.set_attribute("research.report_validation_status", str(validation_status))
            span.set_attribute("research.latency_seconds", latency)
            if metrics["total_tokens"]:
                span.set_attribute("research.total_tokens", int(metrics["total_tokens"]))
            if metrics["cost_usd"] is not None:
                span.set_attribute("research.cost_usd", float(metrics["cost_usd"]))

        self._status_repository.update_status(
            job_id,
            "FAILED",
            error=f"Output blocked: {dominant}",
            metadata_update={
                "failure_summary": failure_summary,
                "report_validation_status": validation_status,
                "latency_seconds": latency,
                "tokens_used": metrics["total_tokens"] or None,
                "cost_usd": metrics["cost_usd"],
                "cost_reconciliation": reconciliation,
                "side_op_failures": side_op_failures or None,
            },
        )

    def _mark_completed(
        self,
        job_id: str,
        *,
        md_uri: str,
        latency: float,
        metrics: dict,
        pdf_available: bool,
        side_op_failures: dict,
        reconciliation: dict | None = None,
    ) -> None:
        """Mark a job as completed with reconciled metadata."""
        metadata = build_completion_metadata(
            latency=latency,
            metrics=metrics,
            pdf_available=pdf_available,
            side_op_failures=side_op_failures,
            reconciliation=reconciliation,
        )
        self._status_repository.update_status(
            job_id,
            "COMPLETED",
            gcs_uri=md_uri,
            progress=100,
            current_step="Completed",
            metadata_update=metadata,
        )
        logger.info(f"[Pipeline] Research completed successfully for job {job_id}")

    def _handle_failure(self, error: Exception, job_id: str, span: Span | None) -> None:
        """Mark failed jobs with normalized error context."""
        error_msg = str(error)
        if "GeneratorExit" in error_msg or "TaskGroup" in error_msg:
            error_msg = "Parallel execution collapsed (likely Quota/QPM limit reached)"

        if span is not None:
            span.record_exception(error)
            span.set_attribute("research.status", "failed")

        logger.error(
            f"[Pipeline] Error processing research for job_id={job_id}: {error}"
        )
        self._status_repository.update_status(
            job_id,
            "FAILED",
            error=error_msg,
            metadata_update={"raw_error": str(error)[:1000]},
        )
