"""Tests for internal Cloud Tasks routes (src/routes/internal_tasks.py)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.worker.api.auth import require_cloud_tasks_oidc
from src.worker.dependencies import get_research_task_handler
from src.worker.main import app


@pytest.fixture
def worker_client():
    mock_handler = MagicMock()
    mock_handler.handle = AsyncMock(
        return_value={"job_id": "job_123", "status": "COMPLETED", "action": "ran"}
    )

    app.dependency_overrides[require_cloud_tasks_oidc] = lambda: {
        "email": "sa@project.iam.gserviceaccount.com"
    }
    app.dependency_overrides[get_research_task_handler] = lambda: mock_handler

    with TestClient(app) as client:
        client.mock_handler = mock_handler
        yield client

    app.dependency_overrides.clear()


def test_research_task_route_happy_path(worker_client):
    payload = {
        "job_id": "job_123",
        "company_name": "Acme Corp",
        "metadata": {"user_id": "user@colt.net"},
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
    }
    response = worker_client.post("/internal/tasks/research", json=payload)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "job_id": "job_123",
        "status": "COMPLETED",
        "action": "ran",
    }
    assert worker_client.mock_handler.handle.called


def test_research_task_route_exception_returns_500(worker_client):
    worker_client.mock_handler.handle.side_effect = RuntimeError(
        "Worker pipeline crashed"
    )
    payload = {
        "job_id": "job_123",
        "company_name": "Acme Corp",
    }
    response = worker_client.post("/internal/tasks/research", json=payload)
    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert "Pipeline failed" in response.json()["detail"]


def test_research_task_route_not_found_returns_200(worker_client):
    worker_client.mock_handler.handle.side_effect = None
    worker_client.mock_handler.handle.return_value = {
        "job_id": "job_123",
        "status": "not_found",
        "action": "noop",
    }
    payload = {
        "job_id": "job_123",
        "company_name": "Acme Corp",
    }
    response = worker_client.post("/internal/tasks/research", json=payload)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "job_id": "job_123",
        "status": "not_found",
        "action": "noop",
    }
