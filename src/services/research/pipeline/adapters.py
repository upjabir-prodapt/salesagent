"""Adapters that bind concrete services to research infrastructure ports."""

from __future__ import annotations

from typing import Any

from ....repositories.bigquery_repository import BigQueryRepository
from ..artifacts.service import ResearchArtifactService
from ..finalization.service import ResearchFinalizationService
from ..run.runner import ResearchRunnerService


class BigQueryStatusAdapter:
    """Status repository adapter over BigQueryRepository."""

    def __init__(self, repository: BigQueryRepository) -> None:
        self._repository = repository

    def update_status(
        self,
        job_id: str,
        status: str | None,
        *,
        gcs_uri: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        metadata_update: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> bool:
        return self._repository.update_status(
            job_id,
            status,
            gcs_uri=gcs_uri,
            progress=progress,
            current_step=current_step,
            metadata_update=metadata_update,
            error=error,
        )


class AdkRunnerAdapter:
    """Agent runtime adapter over ResearchRunnerService."""

    def __init__(self, runner_service: ResearchRunnerService) -> None:
        self._runner_service = runner_service

    async def run(self, job_id: str, company_name: str) -> tuple[str, dict]:
        return await self._runner_service.run(job_id, company_name)


class GcsArtifactAdapter:
    """Artifact adapter over ResearchArtifactService."""

    def __init__(self, artifact_service: ResearchArtifactService) -> None:
        self._artifact_service = artifact_service

    def upload_artifacts(
        self, job_id: str, final_report: str, session_state: dict
    ) -> str:
        return self._artifact_service.upload_artifacts(
            job_id, final_report, session_state
        )

    async def upload_agent_artifacts(
        self, job_id: str, session_state: dict
    ) -> dict[str, str]:
        return await self._artifact_service.upload_agent_artifacts(
            job_id, session_state
        )


class FinalizationAdapter:
    """Finalization adapter over ResearchFinalizationService."""

    def __init__(self, finalization_service: ResearchFinalizationService) -> None:
        self._finalization_service = finalization_service

    async def finalize(
        self,
        job_id: str,
        final_report: str,
        session_state: dict,
        metrics: dict,
        metadata: dict | None = None,
    ) -> tuple[dict, bool]:
        return await self._finalization_service.finalize(
            job_id, final_report, session_state, metrics, metadata=metadata
        )

    async def export_failure_telemetry(
        self,
        job_id: str,
        session_state: dict,
        metrics: dict,
    ) -> dict[str, str]:
        return await self._finalization_service.export_failure_telemetry(
            job_id, session_state, metrics
        )
