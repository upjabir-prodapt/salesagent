import json
from datetime import timedelta
from typing import Any

from google.cloud import storage

from ..core.clients import client_pool
from ..core.config import settings
from ..core.exceptions import StorageError
from ..core.logging_config import logger


class GCSRepository:
    """Repository for Google Cloud Storage operations"""

    def __init__(self, client: storage.Client = None):
        self.client = client or client_pool.get_storage_client()
        self.bucket_name = settings.GCS_BUCKET_NAME
        self.bucket = self.client.bucket(self.bucket_name)

    def ensure_bucket_exists(self) -> bool:
        """Create the bucket if it doesn't exist"""
        try:
            if not self.bucket.exists():
                logger.info(f"Creating GCS bucket: {self.bucket_name}")
                self.client.create_bucket(
                    self.bucket, location=settings.GOOGLE_CLOUD_LOCATION
                )
                logger.info(f"Created GCS bucket: {self.bucket_name}")
            else:
                logger.info(f"GCS bucket already exists: {self.bucket_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to ensure bucket exists: {e}")
            raise StorageError(f"Failed to ensure bucket exists: {e}") from e

    def upload_json(self, request_id: str, data: dict[str, Any]) -> str:
        """Upload JSON data to GCS"""
        try:
            blob_name = f"{settings.GCS_PARENT_FOLDER}/{request_id}/raw_data.json"
            blob = self.bucket.blob(blob_name)
            blob.upload_from_string(
                data=json.dumps(data, indent=2), content_type="application/json"
            )
            logger.info(
                f"Uploaded JSON data to GCS: gs://{self.bucket_name}/{blob_name}"
            )
            return f"gs://{self.bucket_name}/{blob_name}"
        except Exception as e:
            logger.error(f"Unexpected error uploading JSON: {e}")
            raise StorageError(f"Unexpected storage error: {e}") from e

    def upload_markdown(self, request_id: str, content: str) -> str:
        """Upload markdown report to GCS"""
        try:
            blob_name = f"{settings.GCS_PARENT_FOLDER}/{request_id}/final_report.md"
            blob = self.bucket.blob(blob_name)
            blob.upload_from_string(data=content, content_type="text/markdown")
            logger.info(
                f"Uploaded markdown report to GCS: gs://{self.bucket_name}/{blob_name}"
            )
            return f"gs://{self.bucket_name}/{blob_name}"
        except Exception as e:
            logger.error(f"Unexpected error uploading markdown: {e}")
            raise StorageError(f"Unexpected storage error: {e}") from e

    def upload_pdf(self, request_id: str, pdf_bytes: bytes) -> str:
        """Upload PDF report to GCS"""
        try:
            blob_name = f"{settings.GCS_PARENT_FOLDER}/{request_id}/final_report.pdf"
            blob = self.bucket.blob(blob_name)
            blob.upload_from_string(data=pdf_bytes, content_type="application/pdf")
            logger.info(
                f"Uploaded PDF report to GCS: gs://{self.bucket_name}/{blob_name}"
            )
            return f"gs://{self.bucket_name}/{blob_name}"
        except Exception as e:
            logger.error(f"Unexpected error uploading PDF: {e}")
            raise StorageError(f"Unexpected storage error: {e}") from e

    def upload_evaluation(
        self, request_id: str, evaluation_data: dict[str, Any]
    ) -> str:
        """Upload evaluation JSON results to GCS"""
        try:
            blob_name = f"{settings.GCS_PARENT_FOLDER}/{request_id}/evaluation.json"
            blob = self.bucket.blob(blob_name)
            blob.upload_from_string(
                data=json.dumps(evaluation_data, indent=2),
                content_type="application/json",
            )
            logger.info(
                f"Uploaded evaluation results to GCS: gs://{self.bucket_name}/{blob_name}"
            )
            return f"gs://{self.bucket_name}/{blob_name}"
        except Exception as e:
            logger.error(f"Unexpected error uploading evaluation: {e}")
            raise StorageError(f"Unexpected storage error: {e}") from e

    def download_json(self, request_id: str) -> dict[str, Any] | None:
        """Download JSON data from GCS"""
        try:
            blob_name = f"{settings.GCS_PARENT_FOLDER}/{request_id}/raw_data.json"
            blob = self.bucket.blob(blob_name)
            if not blob.exists():
                return None
            return json.loads(blob.download_as_string())
        except Exception as e:
            logger.error(f"Unexpected error downloading JSON: {e}")
            return None

    def download_markdown(self, request_id: str) -> str | None:
        """Download markdown report from GCS"""
        try:
            blob_name = f"{settings.GCS_PARENT_FOLDER}/{request_id}/final_report.md"
            blob = self.bucket.blob(blob_name)
            if not blob.exists():
                return None
            return blob.download_as_string().decode("utf-8")
        except Exception as e:
            logger.error(f"Unexpected error downloading markdown: {e}")
            return None

    def get_signed_url(
        self, blob_name_or_uri: str, expiration: timedelta = timedelta(hours=1)
    ) -> str | None:
        """Generate a signed URL for a GCS object"""
        try:
            blob_name = blob_name_or_uri
            if blob_name_or_uri.startswith("gs://"):
                # Remove 'gs://bucket_name/' from the string
                parts = blob_name_or_uri[5:].split("/", 1)
                if len(parts) < 2:
                    return None
                blob_name = parts[1]

            blob = self.bucket.blob(blob_name)
            return blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                method="GET",
            )
        except Exception as e:
            logger.error(f"Unexpected error generating signed URL: {e}")
            return None

    def download_pdf(self, request_id: str) -> bytes | None:
        """Download PDF report bytes from GCS"""
        try:
            blob_name = f"{settings.GCS_PARENT_FOLDER}/{request_id}/final_report.pdf"
            blob = self.bucket.blob(blob_name)
            if not blob.exists():
                return None
            return blob.download_as_bytes()
        except Exception as e:
            logger.error(f"Unexpected error downloading PDF: {e}")
            return None

    def delete_request_data(self, request_id: str) -> bool:
        """Delete all blobs associated with a request_id"""
        try:
            prefix = f"{settings.GCS_PARENT_FOLDER}/{request_id}/"
            blobs = self.bucket.list_blobs(prefix=prefix)
            self.bucket.delete_blobs(list(blobs))
            return True
        except Exception as e:
            logger.error(f"Unexpected error deleting request data: {e}")
            return False
