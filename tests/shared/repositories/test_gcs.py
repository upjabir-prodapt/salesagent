from unittest.mock import MagicMock

import pytest

from src.shared.exceptions import StorageError
from src.shared.repositories.gcs_repository import GCSRepository


def test_upload_json_success(mock_storage_client, mock_settings):
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    repo = GCSRepository(client=mock_storage_client)

    data = {"key": "value"}
    result = repo.upload_json(request_id="test-job", data=data)

    assert result == "gs://test-bucket/research/test-job/raw_data.json"
    mock_bucket.blob.assert_called_once_with("research/test-job/raw_data.json")
    mock_blob.upload_from_string.assert_called_once()
    args, kwargs = mock_blob.upload_from_string.call_args
    assert kwargs["content_type"] == "application/json"


def test_upload_markdown_success(mock_storage_client, mock_settings):
    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_bucket.blob.return_value = mock_blob

    repo = GCSRepository(client=mock_storage_client)

    content = "# Report"
    result = repo.upload_markdown(request_id="test-job", content=content)

    assert result == "gs://test-bucket/research/test-job/final_report.md"
    mock_blob.upload_from_string.assert_called_once_with(
        data=content, content_type="text/markdown"
    )


def test_upload_json_failure(mock_storage_client, mock_settings):
    mock_bucket = MagicMock()
    mock_storage_client.bucket.return_value = mock_bucket
    mock_bucket.blob.side_effect = Exception("Storage Error")

    repo = GCSRepository(client=mock_storage_client)

    with pytest.raises(StorageError):
        repo.upload_json(request_id="test-job", data={})
