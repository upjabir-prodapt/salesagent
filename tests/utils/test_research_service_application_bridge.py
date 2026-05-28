from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.services.research.research_service import ResearchService


@pytest.mark.asyncio
async def test_research_service_delegates_background_processing_to_application_layer() -> None:
    bigquery_repo = MagicMock()
    gcs_repo = MagicMock()
    service = ResearchService(bigquery_repository=bigquery_repo, gcs_repository=gcs_repo)

    called: dict[str, str] = {}

    async def _run_background_job(command, *, span=None):
        called["job_id"] = command.job_id
        called["company_name"] = command.company_name

    service._application.run_background_job = _run_background_job  # type: ignore[attr-defined]

    await service.process_research_background("job-123", "Acme Corp", metadata={})

    assert called["job_id"] == "job-123"
    assert called["company_name"] == "Acme Corp"
