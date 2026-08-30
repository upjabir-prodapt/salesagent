"""Research job CRUD and status service for the API role."""

from __future__ import annotations

import uuid
from typing import Any

from src.shared.config import settings
from src.shared.exceptions import ResourceNotFoundError, ServiceError
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

    def _assert_owner(
        self,
        job_data: dict[str, Any] | None,
        job_id: str,
        user_email: str | None = None,
    ) -> None:
        """Reject access to a job belonging to another user.

        Raises ResourceNotFoundError (404), not 403, so endpoints cannot be used
        to enumerate job IDs belonging to other users.
        """
        if job_data is None or not user_email:
            return

        owner = job_data.get("user_id") or (job_data.get("metadata") or {}).get(
            "user_id"
        )
        if owner and owner != user_email:
            logger.warning(
                "Rejected access to job %s: ownership mismatch (owner=%s, requester=%s)",
                job_id,
                owner,
                user_email,
            )
            raise ResourceNotFoundError(f"Job {job_id} not found")

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

    def get_request_status(
        self, job_id: str, user_email: str | None = None
    ) -> dict[str, Any] | None:
        """Get the current status of a research job owned by user_email."""
        try:
            status_data = self.bigquery_repo.get_status(job_id)
            self._assert_owner(status_data, job_id, user_email)
            return status_data
        except ResourceNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            raise ServiceError(f"Failed to get job status: {str(e)}") from e

    def get_pdf_report(
        self, job_id: str, user_email: str | None = None
    ) -> tuple[bytes, str] | None:
        """Return (pdf_bytes, company_name) for a COMPLETED job owned by user_email."""
        try:
            status_data = self.bigquery_repo.get_status(job_id)
        except Exception as e:
            raise ServiceError(f"Failed to fetch job status: {str(e)}") from e

        if status_data is None:
            return None

        self._assert_owner(status_data, job_id, user_email)

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

    def get_request_result(
        self, job_id: str, user_email: str | None = None
    ) -> dict[str, Any] | None:
        """Get the result of a completed research job owned by user_email."""
        try:
            result = self.bigquery_repo.get_request_result(
                job_id, gcs_repository=self.gcs_repo
            )
            if result is None:
                return None

            self._assert_owner(result, job_id, user_email)

            meta = result.get("metadata") or {}
            result["model_card"] = build_model_card(meta)
            return result
        except ResourceNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to get job result: {e}")
            raise ServiceError(f"Failed to get job result: {str(e)}") from e

    def list_jobs(
        self, user_email: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List research jobs owned by current user."""
        try:
            return self.bigquery_repo.list_jobs_for_user(
                user_email=user_email, limit=limit, offset=offset
            )
        except Exception as e:
            logger.error(f"Failed to list jobs for user {user_email}: {e}")
            raise ServiceError(f"Failed to list research jobs: {str(e)}") from e

    def cancel_job(self, job_id: str, user_email: str) -> bool:
        """Cancel an in-progress research job owned by current user."""
        try:
            success = self.bigquery_repo.cancel_job(job_id, user_email=user_email)
            if not success:
                raise ResourceNotFoundError(
                    f"Job {job_id} not found, already finished, or not owned by user"
                )
            return True
        except ResourceNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
            raise ServiceError(f"Failed to cancel research job: {str(e)}") from e

    def submit_feedback(self, job_id: str, feedback: str, user_email: str) -> bool:
        """Submit feedback for a completed research job."""
        try:
            status_data = self.bigquery_repo.get_status(job_id)
            self._assert_owner(status_data, job_id, user_email)
            return self.bigquery_repo.insert_user_feedback(job_id, user_email, feedback)
        except ResourceNotFoundError:
            raise
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
        del trace_context_headers  # tracing handled by the caller's own span
        from src.worker.dependencies import build_research_pipeline
        from src.worker.services.artifacts import ResearchArtifactService
        from src.worker.services.finalization_service import (
            ResearchFinalizationService,
        )
        from src.worker.services.job_runner import ResearchJobRunner

        runner = ResearchJobRunner(
            pipeline=build_research_pipeline(),
            bigquery_repository=self.bigquery_repo,
            artifact_service=ResearchArtifactService(self.bigquery_repo, self.gcs_repo),
            finalization_service=ResearchFinalizationService(
                self.bigquery_repo, self.gcs_repo
            ),
        )
        await runner.run(job_id, company_name, metadata=metadata)
