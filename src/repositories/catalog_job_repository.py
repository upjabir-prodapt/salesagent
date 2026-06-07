"""BigQuery repository for catalog build jobs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery
from google.cloud.exceptions import GoogleCloudError

from ..core.config import settings
from ..core.exceptions import DatabaseError
from ..core.logging_config import logger
from ..dependencies.service_dependencies import get_bigquery_client


class CatalogJobRepository:
    """Persist catalog pipeline job status in BigQuery."""

    def __init__(self, client: bigquery.Client | None = None) -> None:
        self.client = client or get_bigquery_client()
        self.table_ref = settings.bigquery_catalog_jobs_table_ref

    def _execute(
        self,
        query: str,
        params: list[bigquery.ScalarQueryParameter],
        operation: str,
    ) -> Any:
        if self.client is None:
            return []
        try:
            job_config = bigquery.QueryJobConfig(query_parameters=params)
            return self.client.query(query, job_config=job_config).result()
        except GoogleCloudError as exc:
            logger.error("BigQuery %s failed: %s", operation, exc)
            raise DatabaseError(f"Failed to {operation}: {exc}") from exc

    def create_job(
        self,
        job_id: str,
        operation: str,
        user_email: str,
        *,
        version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if self.client is None:
            logger.info("Local bypass: catalog job %s %s", job_id, operation)
            return True
        now = datetime.now(UTC)
        query = f"""
        INSERT INTO `{self.table_ref}` (
            job_id, operation, status, progress, current_step,
            version_id, error_message, user_email, created_at, updated_at, metadata
        )
        VALUES (
            @job_id, @operation, @status, @progress, @current_step,
            @version_id, @error_message, @user_email, @created_at, @updated_at, @metadata
        )
        """
        params = [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("operation", "STRING", operation),
            bigquery.ScalarQueryParameter("status", "STRING", "PENDING"),
            bigquery.ScalarQueryParameter("progress", "INT64", 0),
            bigquery.ScalarQueryParameter("current_step", "STRING", "Queued"),
            bigquery.ScalarQueryParameter("version_id", "STRING", version_id),
            bigquery.ScalarQueryParameter("error_message", "STRING", None),
            bigquery.ScalarQueryParameter("user_email", "STRING", user_email),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", now),
            bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", now),
            bigquery.ScalarQueryParameter(
                "metadata",
                "JSON",
                json.dumps(metadata) if metadata else None,
            ),
        ]
        self._execute(query, params, "create catalog job")
        return True

    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        version_id: str | None = None,
        error_message: str | None = None,
        metadata_update: dict[str, Any] | None = None,
    ) -> bool:
        if self.client is None:
            return True
        now = datetime.now(UTC)
        fields = ["updated_at = @updated_at"]
        params: list[bigquery.ScalarQueryParameter] = [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", now),
        ]
        if status is not None:
            fields.append("status = @status")
            params.append(bigquery.ScalarQueryParameter("status", "STRING", status))
        if progress is not None:
            fields.append("progress = @progress")
            params.append(bigquery.ScalarQueryParameter("progress", "INT64", progress))
        if current_step is not None:
            fields.append("current_step = @current_step")
            params.append(
                bigquery.ScalarQueryParameter("current_step", "STRING", current_step)
            )
        if version_id is not None:
            fields.append("version_id = @version_id")
            params.append(
                bigquery.ScalarQueryParameter("version_id", "STRING", version_id)
            )
        if error_message is not None:
            fields.append("error_message = @error_message")
            params.append(
                bigquery.ScalarQueryParameter("error_message", "STRING", error_message)
            )
        if metadata_update is not None:
            fields.append("metadata = @metadata")
            params.append(
                bigquery.ScalarQueryParameter(
                    "metadata", "JSON", json.dumps(metadata_update)
                )
            )

        query = f"""
        UPDATE `{self.table_ref}`
        SET {", ".join(fields)}
        WHERE job_id = @job_id
        """
        self._execute(query, params, "update catalog job")
        return True

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        if self.client is None:
            return {
                "job_id": job_id,
                "operation": "rebuild",
                "status": "COMPLETED",
                "progress": 100,
                "current_step": "Done",
                "version_id": "local",
            }
        query = f"""
        SELECT job_id, operation, status, progress, current_step,
               version_id, error_message, user_email, created_at, updated_at, metadata
        FROM `{self.table_ref}`
        WHERE job_id = @job_id
        LIMIT 1
        """
        params = [bigquery.ScalarQueryParameter("job_id", "STRING", job_id)]
        rows = list(self._execute(query, params, "get catalog job"))
        if not rows:
            return None
        row = rows[0]
        meta = row.metadata
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else {}
        return {
            "job_id": row.job_id,
            "operation": row.operation,
            "status": row.status,
            "progress": row.progress,
            "current_step": row.current_step,
            "version_id": row.version_id,
            "error_message": row.error_message,
            "user_email": row.user_email,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "metadata": meta or {},
        }
