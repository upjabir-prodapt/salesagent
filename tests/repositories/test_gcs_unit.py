from unittest.mock import MagicMock

import pytest

from src.repositories.gcs_repository import GCSRepository


@pytest.fixture
def mock_storage_client():
    return MagicMock()


@pytest.fixture
def repo(mock_storage_client, mock_settings):
    return GCSRepository(client=mock_storage_client)


def test_ensure_bucket_exists(repo, mock_storage_client):
    repo.ensure_bucket_exists()
    mock_storage_client.bucket.assert_called()


def test_upload_json(repo, mock_storage_client):
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value.blob.return_value = mock_blob

    uri = repo.upload_json("job_123", {"data": "test"})
    assert "gs://" in uri
    mock_blob.upload_from_string.assert_called()


def test_download_json_not_found(repo, mock_storage_client):
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_storage_client.bucket.return_value.blob.return_value = mock_blob

    result = repo.download_json("job_123")
    assert result is None
