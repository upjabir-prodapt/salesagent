"""Research pipeline execution service for the Worker role."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

from src.shared.logging_config import contextualize, logger
from src.shared.repositories.bigquery_repository import BigQueryRepository
from src.shared.repositories.gcs_repository import GCSRepository
from src.shared.utils.tracing import job_attrs, traced_with_context

from ..evaluation.service import EvaluationService
from ..runtime.runner import ResearchRunnerService
from .artifacts import ResearchArtifactService
from .finalization_service import ResearchFinalizationService
from .orchestrator import (
    AdkRunnerAdapter,
    BigQueryStatusAdapter,
    FinalizationAdapter,
    GcsArtifactAdapter,
    ResearchApplicationService,
    ResearchJobCommand,
    ResearchJobOrchestrator,
)


class ResearchPipelineService:
    """Service orchestrating the background ADK research swarm pipeline."""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
        *,
        runner_service: ResearchRunnerService | None = None,
        artifact_service: ResearchArtifactService | None = None,
        finalization_service: ResearchFinalizationService | None = None,
        evaluation_service: EvaluationService | None = None,
    ) -> None:
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

    @traced_with_context("research.background.process", attributes=job_attrs)
    async def process_research_background(
        self,
        job_id: str,
        company_name: str,
        *,
        metadata: dict[str, Any] | None = None,
        trace_context_headers: dict[str, str] | None = None,
    ) -> None:
        """Run the research swarm and compile artifacts in background."""
        span = trace.get_current_span()
        span.set_attribute("research.company_name", company_name)
        span.set_attribute("research.job_id", job_id)

        context_metadata = {"trace_id": job_id}
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
