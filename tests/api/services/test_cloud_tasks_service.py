"""Tests for CloudTasksService (src/services/research/cloud_tasks_service.py)."""

from unittest.mock import MagicMock, patch

import pytest
from google.api_core import exceptions as gcp_exceptions

from src.api.services.cloud_tasks_service import CloudTasksService


def test_enqueue_research_local_http_bypass():
    mock_client = MagicMock()
    service = CloudTasksService(client=mock_client)

    with (
        patch("src.api.services.cloud_tasks_service.settings") as mock_settings,
        patch("src.api.services.cloud_tasks_service.requests.post") as mock_post,
    ):
        mock_settings.IS_LOCAL = True
        mock_settings.CLOUD_TASKS_WORKER_URL = (
            "http://127.0.0.1:8001/internal/tasks/research"
        )
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        task_name = service.enqueue_research(
            job_id="job_123",
            company_name="Acme Corp",
            metadata={"user_id": "test@colt.net"},
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        )

        assert task_name == "local-http/job_123"
        assert mock_post.called
        assert not mock_client.create_task.called


def test_enqueue_research_creates_cloud_task():
    mock_client = MagicMock()
    mock_client.queue_path.return_value = (
        "projects/test-proj/locations/europe-west1/queues/research-jobs"
    )
    mock_created = MagicMock()
    mock_created.name = "projects/test-proj/locations/europe-west1/queues/research-jobs/tasks/research-job_123"
    mock_client.create_task.return_value = mock_created

    service = CloudTasksService(client=mock_client)

    with patch("src.api.services.cloud_tasks_service.settings") as mock_settings:
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_TASKS_PROJECT = "test-proj"
        mock_settings.CLOUD_TASKS_LOCATION = "europe-west1"
        mock_settings.CLOUD_TASKS_QUEUE = "research-jobs"
        mock_settings.CLOUD_TASKS_WORKER_URL = (
            "https://worker.run.app/internal/tasks/research"
        )
        mock_settings.CLOUD_TASKS_OIDC_SERVICE_ACCOUNT = (
            "sa@project.iam.gserviceaccount.com"
        )
        mock_settings.CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS = 1800
        mock_settings.WORKER_OIDC_AUDIENCE = ""

        task_name = service.enqueue_research(
            job_id="job_123",
            company_name="Acme Corp",
            metadata={"user_id": "test@colt.net"},
        )

        assert task_name == mock_created.name
        assert mock_client.create_task.called
        call_args = mock_client.create_task.call_args[1]["request"]
        assert (
            call_args["parent"]
            == "projects/test-proj/locations/europe-west1/queues/research-jobs"
        )
        task = call_args["task"]
        assert (
            task["name"]
            == "projects/test-proj/locations/europe-west1/queues/research-jobs/tasks/research-job_123"
        )
        assert (
            task["http_request"]["url"]
            == "https://worker.run.app/internal/tasks/research"
        )
        assert (
            task["http_request"]["oidc_token"]["service_account_email"]
            == "sa@project.iam.gserviceaccount.com"
        )
        assert (
            task["http_request"]["oidc_token"]["audience"]
            == "https://worker.run.app/internal/tasks/research"
        )


def test_enqueue_research_already_exists_is_idempotent():
    mock_client = MagicMock()
    mock_client.queue_path.return_value = (
        "projects/test-proj/locations/europe-west1/queues/research-jobs"
    )
    mock_client.create_task.side_effect = gcp_exceptions.AlreadyExists("Task exists")

    service = CloudTasksService(client=mock_client)

    with patch("src.api.services.cloud_tasks_service.settings") as mock_settings:
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_TASKS_PROJECT = "test-proj"
        mock_settings.CLOUD_TASKS_LOCATION = "europe-west1"
        mock_settings.CLOUD_TASKS_QUEUE = "research-jobs"
        mock_settings.CLOUD_TASKS_WORKER_URL = (
            "https://worker.run.app/internal/tasks/research"
        )
        mock_settings.CLOUD_TASKS_OIDC_SERVICE_ACCOUNT = (
            "sa@project.iam.gserviceaccount.com"
        )
        mock_settings.CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS = 1800
        mock_settings.WORKER_OIDC_AUDIENCE = ""

        task_name = service.enqueue_research(
            job_id="job_123",
            company_name="Acme Corp",
        )

        assert (
            task_name
            == "projects/test-proj/locations/europe-west1/queues/research-jobs/tasks/research-job_123"
        )


def test_enqueue_research_missing_config_raises():
    mock_client = MagicMock()
    service = CloudTasksService(client=mock_client)

    with patch("src.api.services.cloud_tasks_service.settings") as mock_settings:
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_TASKS_WORKER_URL = ""
        with pytest.raises(RuntimeError) as exc_info:
            service.enqueue_research("job_123", "Acme Corp")
        assert "CLOUD_TASKS_WORKER_URL is not configured" in str(exc_info.value)

    with patch("src.api.services.cloud_tasks_service.settings") as mock_settings:
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_TASKS_WORKER_URL = (
            "https://worker.run.app/internal/tasks/research"
        )
        mock_settings.CLOUD_TASKS_QUEUE = ""
        with pytest.raises(RuntimeError) as exc_info:
            service.enqueue_research("job_123", "Acme Corp")
        assert "CLOUD_TASKS_QUEUE is not configured" in str(exc_info.value)

    with patch("src.api.services.cloud_tasks_service.settings") as mock_settings:
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_TASKS_WORKER_URL = (
            "https://worker.run.app/internal/tasks/research"
        )
        mock_settings.CLOUD_TASKS_QUEUE = "research-jobs"
        mock_settings.CLOUD_TASKS_OIDC_SERVICE_ACCOUNT = ""
        with pytest.raises(RuntimeError) as exc_info:
            service.enqueue_research("job_123", "Acme Corp")
        assert "CLOUD_TASKS_OIDC_SERVICE_ACCOUNT is not configured" in str(
            exc_info.value
        )
