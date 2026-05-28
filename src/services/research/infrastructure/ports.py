"""Port definitions for research infrastructure dependencies."""

from __future__ import annotations

from typing import Any, Protocol


class StatusRepositoryPort(Protocol):
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
    ) -> bool: ...


class AgentRunnerPort(Protocol):
    async def run(self, job_id: str, company_name: str) -> tuple[str, dict]: ...


class ArtifactPort(Protocol):
    def upload_artifacts(self, job_id: str, final_report: str, session_state: dict) -> str: ...

    async def upload_agent_artifacts(
        self, job_id: str, session_state: dict
    ) -> dict[str, str]: ...


class FinalizationPort(Protocol):
    async def finalize(
        self,
        job_id: str,
        final_report: str,
        session_state: dict,
        metrics: dict,
    ) -> tuple[dict, bool]: ...
