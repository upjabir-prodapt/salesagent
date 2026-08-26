from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.worker.services.artifacts import ResearchArtifactService


def test_upload_artifacts_updates_status_and_uploads() -> None:
    bq = MagicMock()
    gcs = MagicMock()
    gcs.upload_markdown.return_value = "gs://bucket/job-1/report.md"
    service = ResearchArtifactService(bq, gcs)

    uri = service.upload_artifacts(
        "job-1", "# Report", {"final_report": "# Report", "strategyagent_output": "x"}
    )

    bq.update_status.assert_called_once()
    gcs.upload_json.assert_called_once_with(
        "job-1", {"final_report": "# Report", "strategyagent_output": "x"}
    )
    assert uri == "gs://bucket/job-1/report.md"


@pytest.mark.asyncio
async def test_upload_agent_artifacts_skips_empty_and_logs_failures() -> None:
    gcs = MagicMock()
    gcs.upload_agent_artifact.side_effect = RuntimeError("gcs")
    service = ResearchArtifactService(MagicMock(), gcs)

    session_state = {
        "strategyagent_output": "strategy text",
        "emptyagent_output": "",
        "other_key": "ignored",
    }

    uris = await service.upload_agent_artifacts("job-1", session_state)

    assert uris == {}
    gcs.upload_agent_artifact.assert_called_once()


@pytest.mark.asyncio
async def test_upload_agent_artifacts_uploads_all_outputs() -> None:
    gcs = MagicMock()
    gcs.upload_agent_artifact.return_value = "gs://bucket/agent.md"
    service = ResearchArtifactService(MagicMock(), gcs)

    uris = await service.upload_agent_artifacts(
        "job-2",
        {"marketagent_output": {"section": "data"}, "risksignals_output": 42},
    )

    assert len(uris) == 2
