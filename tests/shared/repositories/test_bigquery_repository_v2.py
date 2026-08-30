import json
from unittest.mock import MagicMock, patch

import pytest

from src.shared.repositories.bigquery_repository import BigQueryRepository


@pytest.fixture
def bq_repo(mock_bq_client, mock_settings):
    mock_settings.BIGQUERY_COST_ATTRIBUTION_TABLE = "test_cost_attribution"
    return BigQueryRepository(client=mock_bq_client)


def test_insert_cost_attribution_success(bq_repo, mock_bq_client):
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job

    result = bq_repo.insert_cost_attribution(
        job_id="job_123", model_version="gemini-1.5-pro", cost_usd=0.05
    )

    assert result is True
    mock_bq_client.query.assert_called()
    mock_query_job.result.assert_called()


def test_create_request_success(bq_repo, mock_bq_client):
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job

    assert bq_repo.create_request("job_123", "Test Company") is True
    mock_bq_client.query.assert_called()


def test_update_status_partial_fields(bq_repo, mock_bq_client):
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job

    # Test updating only status and progress
    assert bq_repo.update_status("job_123", status="RUNNING", progress=50) is True

    # Verify query was called
    mock_bq_client.query.assert_called()
    query = mock_bq_client.query.call_args[0][0]
    assert "status = @status" in query
    assert "progress = @progress" in query
    assert "gcs_uri =" not in query


def test_get_status_not_found(bq_repo, mock_bq_client):
    mock_bq_client.query.return_value.result.return_value = []

    assert bq_repo.get_status("job_none") is None


def test_get_request_result_completed_with_gcs(bq_repo, mock_bq_client):
    mock_row = MagicMock()
    mock_row.status = "COMPLETED"
    mock_row.gcs_uri = "gs://bucket/job_123.md"
    mock_row.metadata = json.dumps({"test": "data"})

    mock_bq_client.query.return_value.result.return_value = [mock_row]

    with patch("src.shared.repositories.gcs_repository.GCSRepository") as mock_gcs_cls:
        mock_gcs_repo = mock_gcs_cls.return_value
        mock_gcs_repo.get_signed_url.return_value = "http://signed-url"
        mock_gcs_repo.download_markdown.return_value = "# Report Content"

        result = bq_repo.get_request_result("job_123")

        assert result["status"] == "COMPLETED"
        assert result["download_url"] == "http://signed-url"
        assert result["report_content"] == "# Report Content"
        assert result["metadata"] == {"test": "data"}


def test_insert_user_feedback_success(bq_repo, mock_bq_client):
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job

    result = bq_repo.insert_user_feedback(
        job_id="job_123",
        user_email="test@example.com",
        feedback="Highly detailed report!",
    )

    assert result is True
    mock_bq_client.query.assert_called()
    mock_query_job.result.assert_called()
    query = mock_bq_client.query.call_args[0][0]
    assert "INSERT INTO" in query
    assert "users_feedback" in query or "test_users_feedback" in query


def test_list_jobs_for_user_success(bq_repo, mock_bq_client):
    mock_row = MagicMock()
    mock_row.job_execution_id = "job_1"
    mock_row.company_name = "Acme"
    mock_row.status = "COMPLETED"
    mock_row.created_at = None
    mock_row.updated_at = None
    mock_row.error_message = None
    mock_row.progress = 100
    mock_row.metadata = json.dumps({"account_id": "ACC1"})

    mock_bq_client.query.return_value.result.return_value = [mock_row]

    jobs = bq_repo.list_jobs_for_user("user@colt.net", limit=10, offset=0)
    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "job_1"
    assert jobs[0]["company_name"] == "Acme"
    assert jobs[0]["account_id"] == "ACC1"


def test_cancel_job_success(bq_repo, mock_bq_client, mock_settings):
    mock_settings.IS_LOCAL = False
    mock_status_row = MagicMock()
    mock_status_row.company_name = "Acme"
    mock_status_row.status = "PROCESSING"
    mock_status_row.progress = 50
    mock_status_row.current_step = "Running"
    mock_status_row.metadata = json.dumps({"user_id": "user@colt.net"})

    # First call is get_status, second call is update_status
    mock_bq_client.query.return_value.result.side_effect = [
        [mock_status_row],
        [MagicMock()],
    ]

    result = bq_repo.cancel_job("job_1", user_email="user@colt.net")
    assert result is True


def test_cancel_job_rejects_other_user(bq_repo, mock_bq_client, mock_settings):
    mock_settings.IS_LOCAL = False
    mock_status_row = MagicMock()
    mock_status_row.company_name = "Acme"
    mock_status_row.status = "PROCESSING"
    mock_status_row.progress = 50
    mock_status_row.current_step = "Running"
    mock_status_row.metadata = json.dumps({"user_id": "owner@colt.net"})

    mock_bq_client.query.return_value.result.return_value = [mock_status_row]

    result = bq_repo.cancel_job("job_1", user_email="other@colt.net")
    assert result is False
