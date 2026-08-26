from unittest.mock import MagicMock

import pytest

from src.shared.exceptions import DatabaseError
from src.shared.repositories.bigquery_repository import BigQueryRepository


@pytest.fixture
def mock_bq_client():
    return MagicMock()


@pytest.fixture
def repo(mock_bq_client, mock_settings):
    return BigQueryRepository(client=mock_bq_client)


def test_get_status_success(repo, mock_bq_client):
    mock_row = MagicMock()
    mock_row.status = "PENDING"
    mock_row.progress = 0
    mock_row.current_step = "Initializing"

    mock_bq_client.query.return_value.result.return_value = [mock_row]

    result = repo.get_status("job_123")
    assert result["status"] == "PENDING"
    assert result["progress"] == 0


def test_get_status_error(repo, mock_bq_client):
    mock_bq_client.query.side_effect = Exception("Query failed")
    with pytest.raises(DatabaseError):
        repo.get_status("job_123")


def test_insert_cost_attribution(repo, mock_bq_client):
    repo.insert_cost_attribution(
        "job_123", "gemini-pro", 0.7, "v1", 100, 50, 150, 10.5, 0.01
    )
    mock_bq_client.query.assert_called()


def test_insert_agent_telemetry_batch(repo, mock_bq_client):
    mock_bq_client.insert_rows_json.return_value = []
    records = [{"record_id": "1", "job_execution_id": "job_123"}]
    assert repo.insert_agent_telemetry_batch(records) is True


def test_get_request_result_success(repo, mock_bq_client):
    mock_row = MagicMock()
    mock_row.status = "COMPLETED"
    mock_row.gcs_uri = "gs://test-bucket/job_123/report.md"
    mock_row.metadata = None

    mock_bq_client.query.return_value.result.return_value = [mock_row]

    mock_gcs = MagicMock()
    mock_gcs.get_signed_url.return_value = "https://storage.example/signed"
    mock_gcs.download_markdown.return_value = "# Report"

    result = repo.get_request_result("job_123", gcs_repository=mock_gcs)
    assert result is not None
    assert result["status"] == "COMPLETED"
    assert result["report_content"] == "# Report"
    assert result["download_url"] == "https://storage.example/signed"
    mock_gcs.get_signed_url.assert_called_once_with(mock_row.gcs_uri)
    mock_gcs.download_markdown.assert_called_once_with("job_123")
