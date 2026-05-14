import pytest
from unittest.mock import MagicMock, patch
from src.repositories.bigquery_repository import BigQueryRepository
from src.core.exceptions import DatabaseError

@pytest.fixture
def mock_bq_client():
    return MagicMock()

@pytest.fixture
def repo(mock_bq_client, mock_settings):
    return BigQueryRepository(client=mock_bq_client)

def test_ensure_table_exists(repo, mock_bq_client):
    repo.ensure_table_exists()
    mock_bq_client.get_table.assert_called()

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
    repo.insert_cost_attribution("job_123", "gemini-pro", 0.7, "v1", 100, 50, 150, 10.5, ["a.com"], 0.01)
    mock_bq_client.query.assert_called()

def test_insert_agent_telemetry_batch(repo, mock_bq_client):
    records = [{"record_id": "1", "job_execution_id": "job_123"}]
    # insert_agent_telemetry_batch is currently a stub returning True
    assert repo.insert_agent_telemetry_batch(records) is True

def test_get_request_result_success(repo, mock_bq_client):
    mock_row = MagicMock()
    mock_row.items.return_value = [("status", "COMPLETED"), ("report_content", "text")]
    mock_row.status = "COMPLETED"
    mock_row.report_content = "text"
    mock_row.gcs_uri = "gs://..."
    mock_row.model_version = "v1"
    mock_row.tokens_used = 100
    mock_row.latency_seconds = 1.0
    mock_row.cost_usd = 0.01
    
    mock_bq_client.query.return_value.result.return_value = [mock_row]
    
    result = repo.get_request_result("job_123")
    assert result["status"] == "COMPLETED"
