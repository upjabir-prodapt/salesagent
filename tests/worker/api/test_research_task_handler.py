"""Tests for ResearchTaskHandler (src/handlers/research_task_handler.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.shared.schemas.tasks import ResearchTaskPayload
from src.worker.api.handlers import ResearchTaskHandler


@pytest.mark.asyncio
async def test_handle_job_not_found_returns_noop():
    mock_job_runner = MagicMock()
    mock_job_runner.run = AsyncMock()
    mock_bq = MagicMock()
    mock_bq.get_status.return_value = None

    handler = ResearchTaskHandler(
        job_runner=mock_job_runner, bigquery_repository=mock_bq
    )
    payload = ResearchTaskPayload(
        job_id="job_missing",
        company_name="Missing Co",
    )

    result = await handler.handle(payload)
    assert result == {"job_id": "job_missing", "status": "not_found", "action": "noop"}
    assert not mock_job_runner.run.called


@pytest.mark.asyncio
async def test_handle_terminal_status_returns_noop():
    mock_job_runner = MagicMock()
    mock_job_runner.run = AsyncMock()
    mock_bq = MagicMock()
    mock_bq.get_status.return_value = {
        "request_id": "job_done",
        "status": "COMPLETED",
    }

    handler = ResearchTaskHandler(
        job_runner=mock_job_runner, bigquery_repository=mock_bq
    )
    payload = ResearchTaskPayload(
        job_id="job_done",
        company_name="Done Co",
    )

    result = await handler.handle(payload)
    assert result == {"job_id": "job_done", "status": "COMPLETED", "action": "noop"}
    assert not mock_job_runner.run.called


@pytest.mark.asyncio
async def test_handle_runs_pipeline_for_pending_job():
    mock_job_runner = MagicMock()
    mock_job_runner.run = AsyncMock()
    mock_bq = MagicMock()
    mock_bq.get_status.side_effect = [
        {"request_id": "job_123", "status": "PENDING"},
        {"request_id": "job_123", "status": "COMPLETED"},
    ]

    handler = ResearchTaskHandler(
        job_runner=mock_job_runner, bigquery_repository=mock_bq
    )
    payload = ResearchTaskPayload(
        job_id="job_123",
        company_name="Acme Corp",
        metadata={"user_id": "user@colt.net"},
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    )

    result = await handler.handle(payload)
    assert result == {"job_id": "job_123", "status": "COMPLETED", "action": "ran"}
    assert mock_job_runner.run.called
    call_args = mock_job_runner.run.call_args
    assert call_args[0][0] == "job_123"
    assert call_args[0][1] == "Acme Corp"
    assert call_args[1]["metadata"] == {"user_id": "user@colt.net"}
    assert "span" in call_args[1]
