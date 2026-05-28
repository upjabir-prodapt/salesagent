"""PDF generation, evaluation, cost attribution, and telemetry flush."""

from __future__ import annotations

import io

from ...core.logging_config import logger
from ...repositories.bigquery_repository import BigQueryRepository
from ...repositories.gcs_repository import GCSRepository
from ...utils.tracing import job_attrs, traced
from .agent.evaluation_service import EvaluationService
from .finalization_ops import (
    run_cost_attribution_op,
    run_evaluation_op,
    run_pdf_op,
    run_telemetry_flush_op,
)


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

        try:
            pdf_available = await run_pdf_op(
                job_id=job_id,
                final_report=final_report,
                generate_pdf=self.generate_pdf,
                upload_pdf=self._gcs_repo.upload_pdf,
            )
        except Exception as e:
            logger.warning(f"PDF op failed: {e}")
            side_op_failures["pdf"] = str(e)
            pdf_available = False

        try:
            await run_evaluation_op(
                job_id=job_id,
                final_report=final_report,
                session_state=session_state,
                update_status=self._bigquery_repo.update_status,
                evaluate=self._evaluation_service.evaluate,
                upload_evaluation=self._gcs_repo.upload_evaluation,
            )
        except Exception as e:
            logger.warning(f"Eval op failed: {e}")
            side_op_failures["evaluation"] = str(e)

        try:
            await run_cost_attribution_op(
                job_id=job_id,
                session_state=session_state,
                metrics=metrics,
                insert_cost_attribution=self._bigquery_repo.insert_cost_attribution,
            )
        except Exception as e:
            logger.warning(f"Cost op failed: {e}")
            side_op_failures["cost_attribution"] = str(e)

        try:
            await run_telemetry_flush_op(
                job_id=job_id,
                session_state=session_state,
                insert_agent_telemetry_batch=self._bigquery_repo.insert_agent_telemetry_batch,
                upload_deadletter_json=self._gcs_repo.upload_json,
            )
        except Exception as e:
            logger.warning(f"Telemetry op failed: {e}")
            side_op_failures["telemetry"] = str(e)

        return side_op_failures, pdf_available
