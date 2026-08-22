"""PDF generation, evaluation, cost attribution, and telemetry flush."""

from __future__ import annotations

from ....core.logging_config import logger
from ....repositories.bigquery_repository import BigQueryRepository
from ....repositories.firestore_repository import FirestoreSearchCacheRepository
from ....repositories.gcs_repository import GCSRepository
from ....utils.tracing import job_attrs, traced
from .evaluation_service import EvaluationService
from .operations import (
    run_cost_attribution_op,
    run_evaluation_op,
    run_pdf_op,
    run_search_log_op,
    run_telemetry_flush_op,
)

# PyMuPDF Story supports th/td CSS only (not tr-level styling).
_REPORT_PDF_CSS = """
* {
    background-color: transparent !important;
    background: transparent !important;
}
body {
    font-family: Helvetica, Arial, sans-serif;
    line-height: 1.4;
}
@page {
    margin: 1in;
    background-color: white;
}
hr {
    display: none;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 10px;
    margin-bottom: 20px;
    page-break-inside: auto;
}
th, td {
    border: 1px solid #ddd;
    padding: 8px;
    text-align: left;
    font-size: 9pt;
    vertical-align: top;
    background-color: transparent;
}
th {
    background-color: #f2f2f2 !important;
    font-weight: bold;
}
code, pre {
    background-color: transparent !important;
    border: none;
    padding: 0;
    font-family: inherit;
}
blockquote {
    background-color: transparent !important;
    border-left: none;
    margin: 0;
    padding: 0;
}
mark {
    background-color: transparent !important;
}
"""


class ResearchFinalizationService:
    """Run non-fatal side operations after the main research loop."""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
        evaluation_service: EvaluationService | None = None,
        search_cache_repository: FirestoreSearchCacheRepository | None = None,
    ) -> None:
        self._bigquery_repo = bigquery_repository
        self._gcs_repo = gcs_repository
        self._evaluation_service = evaluation_service or EvaluationService()
        self._search_cache_repo = search_cache_repository

    @property
    def search_cache_repo(self) -> FirestoreSearchCacheRepository:
        """Firestore search cache repository, built on first use."""
        if self._search_cache_repo is None:
            self._search_cache_repo = FirestoreSearchCacheRepository()
        return self._search_cache_repo

    @staticmethod
    def generate_pdf(final_report: str) -> bytes:
        """Synchronous helper for PDF generation (CPU-bound)."""
        import markdown
        from weasyprint import CSS, HTML

        html_body = markdown.markdown(
            final_report,
            extensions=["tables", "fenced_code", "nl2br"],
        )
        full_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body>{html_body}</body>
</html>"""

        return HTML(string=full_html).write_pdf(
            stylesheets=[CSS(string=_REPORT_PDF_CSS)]
        )

    @traced("research.finalize", attributes=job_attrs)
    async def finalize(
        self,
        job_id: str,
        final_report: str,
        session_state: dict,
        metrics: dict,
        metadata: dict | None = None,
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
            logger.warning(f"[Pipeline] PDF op failed job_id={job_id}: {e}")
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
            logger.warning(f"[Pipeline] Eval op failed job_id={job_id}: {e}")
            side_op_failures["evaluation"] = str(e)

        try:
            await run_cost_attribution_op(
                job_id=job_id,
                session_state=session_state,
                metrics=metrics,
                metadata=metadata,
                insert_cost_attribution=self._bigquery_repo.insert_cost_attribution,
            )
        except Exception as e:
            logger.warning(f"[Pipeline] Cost op failed job_id={job_id}: {e}")
            side_op_failures["cost_attribution"] = str(e)

        try:
            await run_search_log_op(
                job_id=job_id,
                session_state=session_state,
                insert_search_query_batch=self.search_cache_repo.insert_search_query_batch,
            )
        except Exception as e:
            logger.warning(f"[Pipeline] Search log op failed job_id={job_id}: {e}")
            side_op_failures["search_log"] = str(e)

        try:
            await run_telemetry_flush_op(
                job_id=job_id,
                session_state=session_state,
                insert_agent_telemetry_batch=self._bigquery_repo.insert_agent_telemetry_batch,
                upload_deadletter_json=self._gcs_repo.upload_json,
            )
        except Exception as e:
            logger.warning(f"[Pipeline] Telemetry op failed job_id={job_id}: {e}")
            side_op_failures["telemetry"] = str(e)

        return side_op_failures, pdf_available

    async def export_failure_telemetry(
        self,
        job_id: str,
        session_state: dict,
        metrics: dict,
    ) -> dict[str, str]:
        """Flush telemetry and cost attribution when a job fails before completion."""
        side_op_failures: dict[str, str] = {}

        try:
            await run_cost_attribution_op(
                job_id=job_id,
                session_state=session_state,
                metrics=metrics,
                insert_cost_attribution=self._bigquery_repo.insert_cost_attribution,
            )
        except Exception as e:
            logger.warning(
                f"[Pipeline] Cost op failed on job failure job_id={job_id}: {e}"
            )
            side_op_failures["cost_attribution"] = str(e)

        try:
            await run_search_log_op(
                job_id=job_id,
                session_state=session_state,
                insert_search_query_batch=self.search_cache_repo.insert_search_query_batch,
            )
        except Exception as e:
            logger.warning(f"[Pipeline] Search log op failed job_id={job_id}: {e}")
            side_op_failures["search_log"] = str(e)

        try:
            await run_telemetry_flush_op(
                job_id=job_id,
                session_state=session_state,
                insert_agent_telemetry_batch=self._bigquery_repo.insert_agent_telemetry_batch,
                upload_deadletter_json=self._gcs_repo.upload_json,
            )
        except Exception as e:
            logger.warning(
                f"[Pipeline] Telemetry op failed on job failure job_id={job_id}: {e}"
            )
            side_op_failures["telemetry"] = str(e)

        return side_op_failures
