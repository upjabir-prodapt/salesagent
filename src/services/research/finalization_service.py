"""PDF generation, evaluation, cost attribution, and telemetry flush."""

from __future__ import annotations

import asyncio
import contextlib
import io
from .agent.evaluation_service import EvaluationService
from ...core.config import settings
from ...core.logging_config import logger
from ...repositories.bigquery_repository import BigQueryRepository
from ...repositories.gcs_repository import GCSRepository
from .agent.utils.telemetry import TELEMETRY_RECORDS_KEY
from ...utils.tracing import job_attrs, traced
from .metrics import reconcile_cost
from .retry import with_retry, with_retry_sync


class ResearchFinalizationService:
    """Run non-fatal side operations after the main research loop."""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
        evaluation_service: EvaluationService | None = None,
    ) -> None:
        self._bigquery_repo = bigquery_repository
        self._gcs_repo = gcs_repository
        self._evaluation_service = evaluation_service or EvaluationService()

    @staticmethod
    def generate_pdf(final_report: str) -> bytes:
        """Synchronous helper for PDF generation (CPU-bound)."""
        from markdown_pdf import MarkdownPdf, Section

        pdf = MarkdownPdf(toc_level=0)
        pdf.add_section(Section(final_report))
        pdf_buffer = io.BytesIO()
        pdf.save(pdf_buffer)
        return pdf_buffer.getvalue()

    @traced("research.finalize", attributes=job_attrs)
    async def finalize(
        self,
        job_id: str,
        final_report: str,
        session_state: dict,
        metrics: dict,
    ) -> tuple[dict, bool]:
        """Run PDF, evaluation, cost attribution, and telemetry side operations."""
        side_op_failures: dict[str, str] = {}
        pdf_available = False

        async def _pdf_op():
            nonlocal pdf_available
            pdf_bytes = await asyncio.to_thread(self.generate_pdf, final_report)
            await asyncio.to_thread(self._gcs_repo.upload_pdf, job_id, pdf_bytes)
            pdf_available = True

        try:
            await with_retry(_pdf_op)
        except Exception as e:
            logger.warning(f"PDF op failed: {e}")
            side_op_failures["pdf"] = str(e)

        async def _eval_op():
            self._bigquery_repo.update_status(
                job_id,
                "PROCESSING",
                progress=settings.RESEARCH_EVAL_PROGRESS,
                current_step=settings.RESEARCH_EVAL_STEP_LABEL,
            )
            result = await self._evaluation_service.evaluate(
                job_id, final_report, session_state
            )
            self._gcs_repo.upload_evaluation(job_id, result)

        try:
            await with_retry(_eval_op)
        except Exception as e:
            logger.warning(f"Eval op failed: {e}")
            side_op_failures["evaluation"] = str(e)

        try:
            reconcile_cost(session_state, metrics)
            await with_retry_sync(
                lambda: self._bigquery_repo.insert_cost_attribution(
                    job_id=job_id,
                    model_version=settings.GEMINI_MODEL,
                    temperature=metrics["temperature"],
                    prompt_template_version=settings.PROMPT_TEMPLATE_VERSION,
                    input_tokens=metrics["input_tokens"] or None,
                    output_tokens=metrics["output_tokens"] or None,
                    total_tokens=metrics["total_tokens"] or None,
                    latency_seconds=metrics["latency"],
                    source_domains=metrics["source_domains"] or None,
                    cost_usd=metrics["cost_usd"],
                )
            )
        except Exception as e:
            logger.warning(f"Cost op failed: {e}")
            side_op_failures["cost_attribution"] = str(e)

        telemetry_records = session_state.get(TELEMETRY_RECORDS_KEY) or []
        if telemetry_records:
            try:
                await with_retry_sync(
                    lambda: self._bigquery_repo.insert_agent_telemetry_batch(
                        telemetry_records
                    )
                )
            except Exception as e:
                logger.warning(f"Telemetry op failed: {e}")
                side_op_failures["telemetry"] = str(e)
                with contextlib.suppress(Exception):
                    self._gcs_repo.upload_json(
                        f"{job_id}_telemetry_deadletter",
                        {"records": telemetry_records, "error": str(e)},
                    )

        return side_op_failures, pdf_available
