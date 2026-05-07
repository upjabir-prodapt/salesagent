import pytest
import json
from unittest.mock import MagicMock, patch
from google.cloud.exceptions import NotFound, GoogleCloudError
from src.repositories.bigquery_repository import BigQueryRepository
from src.core.exceptions import DatabaseError

@pytest.fixture
def bq_repo(mock_bq_client, mock_settings):
    # Add missing settings to mock_settings
    mock_settings.BIGQUERY_COST_ATTRIBUTION_TABLE = "test_cost_attribution"
    mock_settings.BIGQUERY_USERS_TABLE = "test_users"
    return BigQueryRepository(client=mock_bq_client)

def test_ensure_table_exists_creation_flow(bq_repo, mock_bq_client):
    # Table and dataset not found initially
    mock_bq_client.get_table.side_effect = NotFound("Table not found")
    mock_bq_client.get_dataset.side_effect = NotFound("Dataset not found")
    
    assert bq_repo.ensure_table_exists() is True
    
    # Verify dataset and table creation
    assert mock_bq_client.create_dataset.called
    assert mock_bq_client.create_table.called

def test_ensure_table_exists_already_exists(bq_repo, mock_bq_client):
    # Table already exists
    mock_bq_client.get_table.return_value = MagicMock()
    
    assert bq_repo.ensure_table_exists() is True
    mock_bq_client.create_table.assert_not_called()

def test_ensure_table_exists_google_cloud_error(bq_repo, mock_bq_client):
    mock_bq_client.get_table.side_effect = GoogleCloudError("API Error")
    
    with pytest.raises(DatabaseError) as excinfo:
        bq_repo.ensure_table_exists()
    assert "Failed to create BigQuery table" in str(excinfo.value)

def test_ensure_cost_attribution_table_exists_creation(bq_repo, mock_bq_client):
    mock_bq_client.get_table.side_effect = NotFound("Not found")
    
    assert bq_repo.ensure_cost_attribution_table_exists() is True
    mock_bq_client.create_table.assert_called()

def test_insert_cost_attribution_success(bq_repo, mock_bq_client):
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job
    
    result = bq_repo.insert_cost_attribution(
        job_id="job_123",
        model_version="gemini-1.5-pro",
        cost_usd=0.05
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
    
    with patch("src.repositories.gcs_repository.GCSRepository") as mock_gcs_cls:
        mock_gcs_repo = mock_gcs_cls.return_value
        mock_gcs_repo.get_signed_url.return_value = "http://signed-url"
        mock_gcs_repo.download_markdown.return_value = "# Report Content"
        
        result = bq_repo.get_request_result("job_123")
        
        assert result["status"] == "COMPLETED"
        assert result["download_url"] == "http://signed-url"
        assert result["report_content"] == "# Report Content"
        assert result["metadata"] == {"test": "data"}

def test_ensure_users_table_exists(bq_repo, mock_bq_client):
    mock_bq_client.get_table.side_effect = NotFound("Not found")
    assert bq_repo.ensure_users_table_exists() is True
    mock_bq_client.create_table.assert_called()

def test_verify_user_success(bq_repo, mock_bq_client):
    mock_row = MagicMock()
    mock_row.email = "test@example.com"
    mock_row.business_unit = "Sales"
    mock_row.organization = "Acme"
    
    mock_bq_client.query.return_value.result.return_value = [mock_row]
    
    result = bq_repo.verify_user("test@example.com", "Sales", "Acme")
    assert result is not None
    assert result["email"] == "test@example.com"

def test_verify_user_not_found(bq_repo, mock_bq_client):
    mock_bq_client.query.return_value.result.return_value = []
    result = bq_repo.verify_user("missing@example.com", "BU", "Org")
    assert result is None
