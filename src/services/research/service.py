"""Research job orchestration service."""

from __future__ import annotations

import uuid
from typing import Any

from opentelemetry import trace

from src.core.config import settings
from src.core.exceptions import ServiceError
from src.core.logging_config import contextualize, logger
from src.repositories.bigquery_repository import BigQueryRepository
from src.repositories.gcs_repository import GCSRepository
from src.utils.tracing import job_attrs, traced_with_context

from .artifacts.service import ResearchArtifactService
from .finalization.evaluation_service import EvaluationService
from .finalization.service import ResearchFinalizationService
from .pipeline import (
    AdkRunnerAdapter,
    BigQueryStatusAdapter,
    FinalizationAdapter,
    GcsArtifactAdapter,
    ResearchApplicationService,
    ResearchJobCommand,
    ResearchJobOrchestrator,
)
from .run.runner import ResearchRunnerService
from .utils.status import build_model_card


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
        self._application = ResearchApplicationService(
            ResearchJobOrchestrator(
                status_repository=BigQueryStatusAdapter(bigquery_repository),
                runner=AdkRunnerAdapter(self._runner),
                artifacts=GcsArtifactAdapter(self._artifacts),
                finalization=FinalizationAdapter(self._finalization),
            )
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

                await self._application.run_background_job(
                    ResearchJobCommand(
                        job_id=job_id,
                        company_name=company_name,
                        metadata=metadata or {},
                    ),
                    span=span,
                )
            except Exception:
                raise

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
