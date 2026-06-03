import pytest

from src.core.exceptions import StorageError
from src.repositories.gcs_repository import GCSRepository


@pytest.fixture
def gcs_repo(mock_storage_client, mock_settings):
    return GCSRepository(client=mock_storage_client)


def test_ensure_bucket_exists_exception(gcs_repo, mock_storage_client):
    mock_bucket = mock_storage_client.bucket.return_value
    mock_bucket.exists.side_effect = Exception("Cloud connection failed")

    with pytest.raises(StorageError) as excinfo:
        gcs_repo.ensure_bucket_exists()
    assert "Failed to ensure bucket exists" in str(excinfo.value)


def test_upload_json_exception(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.upload_from_string.side_effect = Exception("Upload failed")

    with pytest.raises(StorageError) as excinfo:
        gcs_repo.upload_json("job_123", {"data": 1})
    assert "Unexpected storage error" in str(excinfo.value)


def test_upload_markdown_exception(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.upload_from_string.side_effect = Exception("Upload failed")

    with pytest.raises(StorageError) as excinfo:
        gcs_repo.upload_markdown("job_123", "# Content")
    assert "Unexpected storage error" in str(excinfo.value)


def test_upload_pdf_exception(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.upload_from_string.side_effect = Exception("Upload failed")

    with pytest.raises(StorageError) as excinfo:
        gcs_repo.upload_pdf("job_123", b"pdf")
    assert "Unexpected storage error" in str(excinfo.value)


def test_upload_evaluation_exception(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.upload_from_string.side_effect = Exception("Upload failed")

    with pytest.raises(StorageError) as excinfo:
        gcs_repo.upload_evaluation("job_123", {"score": 1})
    assert "Unexpected storage error" in str(excinfo.value)


def test_download_json_not_found(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.exists.return_value = False

    assert gcs_repo.download_json("job_none") is None


def test_download_json_exception(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.exists.return_value = True
    mock_blob.download_as_string.side_effect = Exception("Download failed")

    assert gcs_repo.download_json("job_err") is None


def test_download_markdown_not_found(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.exists.return_value = False

    assert gcs_repo.download_markdown("job_none") is None


def test_download_markdown_exception(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.exists.return_value = True
    mock_blob.download_as_string.side_effect = Exception("Download failed")

    assert gcs_repo.download_markdown("job_err") is None


def test_download_pdf_success(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.exists.return_value = True
    mock_blob.download_as_bytes.return_value = b"%PDF"

    assert gcs_repo.download_pdf("job_123") == b"%PDF"


def test_download_pdf_not_found(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.exists.return_value = False

    assert gcs_repo.download_pdf("job_none") is None


def test_download_pdf_exception(gcs_repo, mock_storage_client):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.exists.return_value = True
    mock_blob.download_as_bytes.side_effect = Exception("Download failed")

    assert gcs_repo.download_pdf("job_err") is None


def test_get_signed_url_invalid_uri(gcs_repo, mock_storage_client):
    assert gcs_repo.get_signed_url("gs://bucket") is None


def test_get_signed_url_uses_iam_signing(gcs_repo, mock_storage_client, monkeypatch):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.generate_signed_url.return_value = "https://storage.example/signed"

    monkeypatch.setattr(
        gcs_repo,
        "_resolve_signing_kwargs",
        lambda: {
            "service_account_email": "sa@test.iam.gserviceaccount.com",
            "access_token": "token",
        },
    )

    url = gcs_repo.get_signed_url("gs://bucket/path/to/blob")

    assert url == "https://storage.example/signed"
    mock_blob.generate_signed_url.assert_called_once()
    kwargs = mock_blob.generate_signed_url.call_args.kwargs
    assert kwargs["service_account_email"] == "sa@test.iam.gserviceaccount.com"
    assert kwargs["access_token"] == "token"


def test_get_signed_url_exception(gcs_repo, mock_storage_client, monkeypatch):
    mock_blob = mock_storage_client.bucket.return_value.blob.return_value
    mock_blob.generate_signed_url.side_effect = Exception("Signed URL failed")
    monkeypatch.setattr(gcs_repo, "_resolve_signing_kwargs", lambda: {})

    assert gcs_repo.get_signed_url("some-blob") is None


def test_delete_request_data_exception(gcs_repo, mock_storage_client):
    mock_storage_client.bucket.return_value.list_blobs.side_effect = Exception(
        "List failed"
    )
    assert gcs_repo.delete_request_data("job_123") is False
