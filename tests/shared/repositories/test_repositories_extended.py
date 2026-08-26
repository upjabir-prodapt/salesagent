from unittest.mock import MagicMock

import pytest

from src.shared.repositories.bigquery_repository import BigQueryRepository
from src.shared.repositories.gcs_repository import GCSRepository


@pytest.fixture
def mock_bq_client():
    return MagicMock()


@pytest.fixture
def bq_repo(mock_bq_client, mock_settings):
    return BigQueryRepository(client=mock_bq_client)


@pytest.fixture
def mock_storage_client():
    return MagicMock()


@pytest.fixture
def gcs_repo(mock_storage_client, mock_settings):
    return GCSRepository(client=mock_storage_client)


# --- BigQueryRepository Tests ---


def test_create_request_success(bq_repo, mock_bq_client):
    mock_bq_client.query.return_value.result.return_value = []
    assert bq_repo.create_request("job_123", "Acme") is True
    mock_bq_client.query.assert_called()


def test_update_status_all_fields(bq_repo, mock_bq_client):
    bq_repo.update_status(
        "job_123",
        "COMPLETED",
        gcs_uri="gs://bucket/file",
        error="none",
        progress=100,
        current_step="Done",
        metadata_update={"a": 1},
    )
    mock_bq_client.query.assert_called()


def test_get_requests_by_status(bq_repo, mock_bq_client):
    mock_row = MagicMock()
    mock_row.job_execution_id = "job_1"
    mock_row.company_name = "Acme"
    mock_row.updated_at = MagicMock()
    mock_row.updated_at.isoformat.return_value = "2023-01-01T00:00:00"

    mock_bq_client.query.return_value.result.return_value = [mock_row]

    results = bq_repo.get_requests_by_status("COMPLETED")
    assert len(results) == 1
    assert results[0]["job_execution_id"] == "job_1"


# --- GCSRepository Tests ---


def test_ensure_bucket_exists_creation(gcs_repo, mock_storage_client):
    """Functional test: Verify bucket is created if it doesn't exist."""
    mock_bucket = MagicMock()
    mock_bucket.exists.return_value = False
    mock_storage_client.bucket.return_value = mock_bucket

    # Re-init repo to pick up the mock client's bucket return value properly
    repo = GCSRepository(client=mock_storage_client)

    repo.ensure_bucket_exists()
    mock_storage_client.create_bucket.assert_called()


def test_ensure_bucket_exists_already_exists(gcs_repo, mock_storage_client):
    """Functional test: Verify bucket is NOT created if it already exists."""
    mock_bucket = MagicMock()
    mock_bucket.exists.return_value = True
    mock_storage_client.bucket.return_value = mock_bucket

    repo = GCSRepository(client=mock_storage_client)

    repo.ensure_bucket_exists()
    mock_storage_client.create_bucket.assert_not_called()


def test_upload_markdown(gcs_repo, mock_storage_client):
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value.blob.return_value = mock_blob
    uri = gcs_repo.upload_markdown("job_123", "# Content")
    assert "final_report.md" in uri
    mock_blob.upload_from_string.assert_called()


def test_upload_pdf(gcs_repo, mock_storage_client):
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value.blob.return_value = mock_blob
    uri = gcs_repo.upload_pdf("job_123", b"%PDF")
    assert "final_report.pdf" in uri
    mock_blob.upload_from_string.assert_called()


def test_upload_evaluation(gcs_repo, mock_storage_client):
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value.blob.return_value = mock_blob
    uri = gcs_repo.upload_evaluation("job_123", {"score": 1})
    assert "evaluation.json" in uri
    mock_blob.upload_from_string.assert_called()


def test_download_markdown(gcs_repo, mock_storage_client):
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.download_as_string.return_value = b"# Content"
    mock_storage_client.bucket.return_value.blob.return_value = mock_blob

    content = gcs_repo.download_markdown("job_123")
    assert content == "# Content"


def test_get_signed_url_gs_uri(gcs_repo, mock_storage_client, monkeypatch):
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value.blob.return_value = mock_blob
    monkeypatch.setattr(gcs_repo, "_resolve_signing_kwargs", lambda: {})
    gcs_repo.get_signed_url("gs://bucket/path/to/blob")
    mock_storage_client.bucket.return_value.blob.assert_called_with("path/to/blob")


def test_delete_request_data(gcs_repo, mock_storage_client):
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value.list_blobs.return_value = [mock_blob]
    assert gcs_repo.delete_request_data("job_123") is True
    mock_storage_client.bucket.return_value.delete_blobs.assert_called()
