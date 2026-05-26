"""Research job orchestration service."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from typing import Any

from opentelemetry import trace

from .agent.evaluation_service import EvaluationService
from .agent.sales.utils.evidence import aggregate_job_evidence
from ...core.config import settings
from ...core.exceptions import ServiceError
from ...core.logging_config import contextualize, logger
from ...repositories.bigquery_repository import BigQueryRepository
from ...repositories.gcs_repository import GCSRepository
from ...utils.guardrails import GuardrailViolation
from ...utils.tracing import job_attrs, traced_with_context
from .artifact_service import ResearchArtifactService
from .finalization_service import ResearchFinalizationService
from .metrics import calculate_metrics, reconcile_cost
from .runner_service import ResearchRunnerService


class ResearchService:
    """Service for handling research operations."""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
        *,
        runner_service: ResearchRunnerService | None = None,
        artifact_service: ResearchArtifactService | None = None,
        finalization_service: ResearchFinalizationService | None = None,
        evaluation_service: EvaluationService | None = None,
    ):
        self.bigquery_repo = bigquery_repository
        self.gcs_repo = gcs_repository
        self._runner = runner_service or ResearchRunnerService(bigquery_repository)
        self._artifacts = artifact_service or ResearchArtifactService(
            bigquery_repository, gcs_repository
        )
        self._finalization = finalization_service or ResearchFinalizationService(
            bigquery_repository,
            gcs_repository,
            evaluation_service=evaluation_service,
        )

    def new_job_id(self) -> str:
        return f"{settings.JOB_ID_PREFIX}{uuid.uuid4()}"

    def create_research_request(
        self, job_id: str, company_name: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        """Create a new research job in the database."""
        try:
            return self.bigquery_repo.create_request(
                job_id=job_id, company_name=company_name, metadata=metadata
            )
        except Exception as e:
            logger.error(f"Failed to create research request: {e}")
            raise ServiceError(f"Failed to create research request: {str(e)}") from e

    @traced_with_context("research.background.process", attributes=job_attrs)
    async def process_research_background(
        self,
        job_id: str,
        company_name: str,
        metadata: dict | None = None,
        trace_context_headers: dict[str, str] | None = None,
    ) -> None:
        """Process research request in background using SalesAgent."""
        span = trace.get_current_span()
        span.set_attribute("research.has_metadata", bool(metadata))
        span.set_attribute("research.status", "started")

        context_metadata = {}
        if metadata:
            context_metadata = {
                "user_email": metadata.get("user_id"),
                "username": metadata.get("username"),
                "business_unit": metadata.get("business_unit"),
                "organization": metadata.get("organization"),
                "trace_id": job_id,
            }

        with contextualize(**context_metadata):
            try:
                logger.info(f"Starting research for job {job_id}: {company_name}")

                self.bigquery_repo.update_status(
                    job_id,
                    "PROCESSING",
                    progress=settings.RESEARCH_INIT_PROGRESS,
                    current_step=settings.RESEARCH_INIT_STEP_LABEL,
                )

                start_time = time.monotonic()

                final_report, session_state = await self._run_research_loop(
                    job_id, company_name
                )
                if not final_report:
                    return

                latency = round(time.monotonic() - start_time, 2)
                span.set_attribute("research.latency_seconds", latency)

                metrics = calculate_metrics(session_state, latency)
                if metrics["total_tokens"]:
                    span.set_attribute(
                        "research.total_tokens", int(metrics["total_tokens"])
                    )
                if metrics["cost_usd"] is not None:
                    span.set_attribute(
                        "research.cost_usd", float(metrics["cost_usd"])
                    )

                reconciliation = reconcile_cost(session_state, metrics)

                md_uri = self._artifacts.upload_artifacts(
                    job_id, final_report, session_state
                )

                try:
                    await self._artifacts.upload_agent_artifacts(job_id, session_state)
                except Exception as e:
                    logger.warning(f"Per-agent artifact upload failed: {e}")

                side_op_failures, pdf_available = await self._finalization.finalize(
                    job_id, final_report, session_state, metrics
                )

                self._mark_completed(
                    job_id,
                    md_uri,
                    latency,
                    metrics,
                    pdf_available,
                    side_op_failures,
                    reconciliation=reconciliation,
                )
                span.set_attribute("research.status", "completed")
            except Exception as e:
                self._handle_failure(e, job_id, span)
                raise

    def _mark_completed(
        self,
        job_id: str,
        md_uri: str,
        latency: float,
        metrics: dict,
        pdf_available: bool,
        side_op_failures: dict,
        reconciliation: dict | None = None,
    ) -> None:
        """Mark job as COMPLETED in the database."""
        meta: dict[str, Any] = {
            "model_version": settings.GEMINI_MODEL,
            "latency_seconds": latency,
            "tokens_used": metrics["total_tokens"] or None,
            "cost_usd": metrics["cost_usd"],
            "pdf_available": pdf_available,
            "side_op_failures": side_op_failures or None,
            "current_agent": None,
        }
        if reconciliation:
            meta["cost_reconciliation"] = reconciliation
        self.bigquery_repo.update_status(
            job_id,
            "COMPLETED",
            gcs_uri=md_uri,
            progress=100,
            current_step="Completed",
            metadata_update=meta,
        )
        logger.info(f"Research completed successfully for job {job_id}")

    def _handle_failure(self, e: Exception, job_id: str, span: Any) -> None:
        """Handle failure during research processing."""
        error_msg = str(e)
        if "GeneratorExit" in error_msg or "TaskGroup" in error_msg:
            error_msg = "Parallel execution collapsed (likely Quota/QPM limit reached)"
        span.record_exception(e)
        span.set_attribute("research.status", "failed")
        logger.error(f"Error processing research for job {job_id}: {e}")
        self.bigquery_repo.update_status(
            job_id,
            "FAILED",
            error=error_msg,
            metadata_update={"raw_error": str(e)[:1000]},
        )

    async def _run_research_loop(
        self, job_id: str, company_name: str
    ) -> tuple[str | None, dict]:
        """Execute SalesAgent; validation runs inside ReportCompiler via AgentTool."""
        final_report, session_state = await self._runner.run(job_id, company_name)

        session_state["job_evidence"] = aggregate_job_evidence(session_state)
        session_state["raw_search_cache"] = session_state["job_evidence"]

        validation_status = session_state.get("report_validation_status")
        if validation_status != "PASSED":
            violations = session_state.get("report_validation_violations") or []
            guard_violations = [
                GuardrailViolation(rule=v.get("rule", "unknown"), detail=v.get("detail", ""))
                for v in violations
                if isinstance(v, dict)
            ]
            failure_summary = self._build_failure_summary(guard_violations)
            dominant = failure_summary.get("dominant_rule", "report_validation_failed")
            logger.error(
                f"[ReportValidation] Job {job_id} blocked: status={validation_status!r}, "
                f"dominant_rule={dominant}"
            )
            self.bigquery_repo.update_status(
                job_id,
                "FAILED",
                error=f"Output blocked: {dominant}",
                metadata_update={
                    "failure_summary": failure_summary,
                    "report_validation_status": validation_status,
                },
            )
            return None, session_state

        return final_report, session_state

    def get_request_status(self, job_id: str) -> dict[str, Any] | None:
        """Get the current status of a research job."""
        try:
            return self.bigquery_repo.get_status(job_id)
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            raise ServiceError(f"Failed to get job status: {str(e)}") from e

    def get_pdf_report(self, job_id: str) -> tuple[bytes, str] | None:
        """
        Return (pdf_bytes, company_name) for a COMPLETED job, or None if not found.
        Raises ServiceError if the job is not yet complete or PDF is missing from GCS.
        """
        try:
            status_data = self.bigquery_repo.get_status(job_id)
        except Exception as e:
            raise ServiceError(f"Failed to fetch job status: {str(e)}") from e

        if status_data is None:
            return None

        if status_data.get("status") != "COMPLETED":
            raise ServiceError(
                f"PDF not available — job status is '{status_data.get('status')}'",
                status_code=409,
            )

        try:
            pdf_bytes = self.gcs_repo.download_pdf(job_id)
        except Exception as e:
            raise ServiceError(f"Failed to download PDF from storage: {str(e)}") from e

        if pdf_bytes is None:
            raise ServiceError("PDF file not found in storage for this job")

        company_name = status_data.get("company_name", job_id)
        return pdf_bytes, company_name

    def get_request_result(self, job_id: str) -> dict[str, Any] | None:
        """Get the result of a completed research job, including cost attribution."""
        try:
            result = self.bigquery_repo.get_request_result(
                job_id, gcs_repository=self.gcs_repo
            )
            if result is None:
                return None

            meta = result.get("metadata") or {}
            result["model_card"] = {
                "model_version": meta.get("model_version"),
                "tokens_used": meta.get("tokens_used"),
                "latency_seconds": meta.get("latency_seconds"),
                "cost_usd": meta.get("cost_usd"),
            }
            return result
        except Exception as e:
            logger.error(f"Failed to get job result: {e}")
            raise ServiceError(f"Failed to get job result: {str(e)}") from e

    @staticmethod
    def _build_failure_summary(violations: list) -> dict:
        """Construct a structured summary of guardrail violations."""
        rule_counts = Counter(v.rule for v in violations)
        dominant_rule = rule_counts.most_common(1)[0][0] if rule_counts else "unknown"
        return {
            "dominant_rule": dominant_rule,
            "all_violations": [
                {"rule": v.rule, "detail": v.detail} for v in violations
            ],
        }
