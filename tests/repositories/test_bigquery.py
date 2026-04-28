import pytest
from unittest.mock import MagicMock
from src.repositories.bigquery_repository import BigQueryRepository
from src.core.exceptions import DatabaseError

def test_create_request_success(mock_bq_client, mock_settings):
    repo = BigQueryRepository(client=mock_bq_client)
    
    # Setup mock return for query execution
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job
    
    result = repo.create_request(job_id="test-job", company_name="Test Company")
    
    assert result is True
    mock_bq_client.query.assert_called_once()
    args, kwargs = mock_bq_client.query.call_args
    assert "INSERT INTO" in args[0]
    assert "test_table" in args[0]

def test_update_status_success(mock_bq_client, mock_settings):
    repo = BigQueryRepository(client=mock_bq_client)
    
    mock_query_job = MagicMock()
    mock_bq_client.query.return_value = mock_query_job
    
    result = repo.update_status(job_id="test-job", status="COMPLETED", progress=100)
    
    assert result is True
    mock_bq_client.query.assert_called_once()
    args, kwargs = mock_bq_client.query.call_args
    assert "UPDATE" in args[0]
    assert "SET" in args[0]
    assert "status = @status" in args[0]
    assert "progress = @progress" in args[0]

def test_create_request_failure(mock_bq_client, mock_settings):
    repo = BigQueryRepository(client=mock_bq_client)
    mock_bq_client.query.side_effect = Exception("BQ Error")
    
    with pytest.raises(DatabaseError):
        repo.create_request(job_id="test-job", company_name="Test Company")
