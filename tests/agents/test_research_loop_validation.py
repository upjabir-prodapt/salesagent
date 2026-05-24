"""Tests for _run_research_loop report validation gate."""

from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from src.services.research.research_service import ResearchService


@pytest.fixture
def service():
    return ResearchService(
        bigquery_repository=MagicMock(),
        gcs_repository=MagicMock(),
    )


@pytest.mark.asyncio
async def test_run_research_loop_fails_when_validation_not_passed(service):
    service._runner.run = AsyncMock(
        return_value=(
            "# Report",
            {"report_validation_status": "FAILED", "report_validation_violations": []},
        )
    )

    report, state = await service._run_research_loop("job_1", "Acme")

    assert report is None
    service.bigquery_repo.update_status.assert_called_with(
        "job_1",
        "FAILED",
        error=ANY,
        metadata_update=ANY,
    )
