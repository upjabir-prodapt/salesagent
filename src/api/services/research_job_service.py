"""Research job CRUD and status service for the API role."""

from __future__ import annotations

import uuid
from typing import Any

from src.shared.config import settings
from src.shared.exceptions import ServiceError
from src.shared.logging_config import logger
from src.shared.repositories.bigquery_repository import BigQueryRepository
from src.shared.repositories.gcs_repository import GCSRepository
from src.worker.services.status import build_model_card


class ResearchJobService:
    """Lightweight service for creating research jobs, polling status, and downloading artifacts."""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
    ) -> None:
        self.bigquery_repo = bigquery_repository
        self.gcs_repo = gcs_repository

    def new_job_id(self) -> str:
        return f"{settings.JOB_ID_PREFIX}{uuid.uuid4()}"

    def create_research_request(
        self, job_id: str, company_name: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        """Create a new research job record in BigQuery."""
        try:
            return self.bigquery_repo.create_request(
                job_id=job_id, company_name=company_name, metadata=metadata
            )
        except Exception as e:
            logger.error(f"Failed to create research request: {e}")
            raise ServiceError(f"Failed to create research request: {str(e)}") from e

    def mark_job_failed(self, job_id: str, error: str) -> None:
        """Mark a job as failed when background task enqueue or dispatch fails."""
        try:
            self.bigquery_repo.update_status(job_id, "FAILED", error=error)
            logger.info(f"Marked job {job_id} as FAILED: {error}")
        except Exception as e:
            logger.error(f"Failed to mark job {job_id} as FAILED: {e}")

    def get_request_status(self, job_id: str) -> dict[str, Any] | None:
        """Get the current status of a research job."""
        try:
            return self.bigquery_repo.get_status(job_id)
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            raise ServiceError(f"Failed to get job status: {str(e)}") from e

    def get_pdf_report(self, job_id: str) -> tuple[bytes, str] | None:
        """Return (pdf_bytes, company_name) for a COMPLETED job, or None if not found."""
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
            result["model_card"] = build_model_card(meta)
            return result
        except Exception as e:
            logger.error(f"Failed to get job result: {e}")
            raise ServiceError(f"Failed to get job result: {str(e)}") from e

    def submit_feedback(self, job_id: str, feedback: str, user_email: str) -> bool:
        """Submit feedback for a completed research job."""
        try:
            status_data = self.bigquery_repo.get_status(job_id)
            if status_data is None:
                return False
            return self.bigquery_repo.insert_user_feedback(job_id, user_email, feedback)
        except Exception as e:
            logger.error(f"Failed to submit feedback for job {job_id}: {e}")
            raise ServiceError(f"Failed to submit feedback: {str(e)}") from e

    async def process_research_background(
        self,
        job_id: str,
        company_name: str,
        *,
        metadata: dict[str, Any] | None = None,
        trace_context_headers: dict[str, str] | None = None,
    ) -> None:
        """Lazy local in-process pipeline runner (for dev mode: API_USE_BACKGROUND_PIPELINE)."""
        from src.worker.services.pipeline_service import (
            ResearchPipelineService,
        )

        pipeline = ResearchPipelineService(self.bigquery_repo, self.gcs_repo)
        await pipeline.process_research_background(
            job_id,
            company_name,
            metadata=metadata,
            trace_context_headers=trace_context_headers,
        )
