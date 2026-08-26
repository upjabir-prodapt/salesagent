from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from src.api.handlers.research_handler import ResearchHandler
from src.api.schemas.research_schemas import ResearchInitiateRequest
from src.shared.exceptions import ResourceNotFoundError, ServiceError


def test_get_research_result_not_found() -> None:
    service = MagicMock()
    service.get_request_result.return_value = None
    handler = ResearchHandler(service)

    with pytest.raises(ResourceNotFoundError):
        handler.get_research_result("missing-job")


def test_download_pdf_report_not_found() -> None:
    service = MagicMock()
    service.get_pdf_report.return_value = None
    handler = ResearchHandler(service)

    with pytest.raises(ResourceNotFoundError):
        handler.download_pdf_report("missing-job")


@pytest.mark.asyncio
async def test_initiate_research_background_tasks_branch() -> None:
    service = MagicMock()
    service.new_job_id.return_value = "job_123"
    service.create_research_request.return_value = True
    bg_tasks = MagicMock(spec=BackgroundTasks)

    handler = ResearchHandler(service)
    request = ResearchInitiateRequest(company_name="Acme Corp", account_id="ACC-123")
    current_user = {
        "email": "user@colt.net",
        "business_unit": "Sales",
        "organization": "Colt",
    }

    with patch("src.api.handlers.research_handler.settings") as mock_settings:
        mock_settings.API_USE_BACKGROUND_PIPELINE = True
        mock_settings.API_PREFIX = "/api/v1"

        response = await handler.initiate_research(request, bg_tasks, current_user)
        assert response.job_id == "job_123"
        assert response.status == "PENDING"
        assert bg_tasks.add_task.called


@pytest.mark.asyncio
async def test_initiate_research_cloud_tasks_branch() -> None:
    service = MagicMock()
    service.new_job_id.return_value = "job_456"
    service.create_research_request.return_value = True
    cloud_tasks = MagicMock()
    cloud_tasks.enqueue_research.return_value = "tasks/123"
    bg_tasks = MagicMock(spec=BackgroundTasks)

    handler = ResearchHandler(service, cloud_tasks_service=cloud_tasks)
    request = ResearchInitiateRequest(company_name="Beta Corp", account_id="ACC-456")
    current_user = {
        "email": "user@colt.net",
        "business_unit": "Sales",
        "organization": "Colt",
    }

    with patch("src.api.handlers.research_handler.settings") as mock_settings:
        mock_settings.API_USE_BACKGROUND_PIPELINE = False
        mock_settings.API_PREFIX = "/api/v1"

        response = await handler.initiate_research(request, bg_tasks, current_user)
        assert response.job_id == "job_456"
        assert response.status == "PENDING"
        assert not bg_tasks.add_task.called
        assert cloud_tasks.enqueue_research.called


@pytest.mark.asyncio
async def test_initiate_research_cloud_tasks_failure_marks_job_failed() -> None:
    service = MagicMock()
    service.new_job_id.return_value = "job_789"
    service.create_research_request.return_value = True
    cloud_tasks = MagicMock()
    cloud_tasks.enqueue_research.side_effect = RuntimeError("Tasks queue full")
    bg_tasks = MagicMock(spec=BackgroundTasks)

    handler = ResearchHandler(service, cloud_tasks_service=cloud_tasks)
    request = ResearchInitiateRequest(company_name="Gamma Corp", account_id="ACC-789")
    current_user = {
        "email": "user@colt.net",
        "business_unit": "Sales",
        "organization": "Colt",
    }

    with patch("src.api.handlers.research_handler.settings") as mock_settings:
        mock_settings.API_USE_BACKGROUND_PIPELINE = False
        mock_settings.API_PREFIX = "/api/v1"

        with pytest.raises(ServiceError) as exc_info:
            await handler.initiate_research(request, bg_tasks, current_user)
        assert "Failed to enqueue research task" in str(exc_info.value)
        assert service.mark_job_failed.called
