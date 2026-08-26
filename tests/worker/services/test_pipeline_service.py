from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.worker.services.pipeline_service import ResearchPipelineService


@pytest.mark.asyncio
async def test_process_research_background_delegates_to_orchestrator():
    bq = MagicMock()
    gcs = MagicMock()
    service = ResearchPipelineService(bq, gcs)
    with patch.object(
        service._application,
        "run_background_job",
        new_callable=AsyncMock,
    ) as mock_run:
        await service.process_research_background(
            "job_1", "Acme", metadata={"user_id": "u"}
        )
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd.job_id == "job_1"
        assert cmd.company_name == "Acme"
        assert cmd.metadata == {"user_id": "u"}
