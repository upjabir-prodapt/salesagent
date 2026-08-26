from __future__ import annotations

from unittest.mock import MagicMock, patch

import src.api.dependencies as api_deps
import src.worker.dependencies as worker_deps
from src.api.handlers.research_handler import ResearchHandler
from src.worker.api.handlers import ResearchTaskHandler


def test_get_research_handler_returns_handler_with_service() -> None:
    mock_service = MagicMock()
    mock_tasks = MagicMock()
    with (
        patch.object(api_deps, "get_research_job_service", return_value=mock_service),
        patch.object(api_deps, "get_cloud_tasks_service", return_value=mock_tasks),
    ):
        handler = api_deps.get_research_handler()

    assert isinstance(handler, ResearchHandler)
    assert handler._service is mock_service
    assert handler._cloud_tasks is mock_tasks


def test_get_research_task_handler_returns_handler_with_service() -> None:
    mock_service = MagicMock()
    mock_bq = MagicMock()
    with (
        patch.object(
            worker_deps, "get_research_pipeline_service", return_value=mock_service
        ),
        patch.object(worker_deps, "get_bigquery_repository", return_value=mock_bq),
    ):
        handler = worker_deps.get_research_task_handler()

    assert isinstance(handler, ResearchTaskHandler)
    assert handler._pipeline is mock_service
    assert handler._bigquery is mock_bq
