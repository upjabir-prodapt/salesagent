from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery
from google.cloud.exceptions import GoogleCloudError

from src.shared.config import settings
from src.shared.exceptions import DatabaseError
from src.shared.logging_config import logger

from .clients import get_bigquery_client


class BigQueryRepository:
    """Repository for BigQuery operations with local bypass support"""

    def __init__(self, client: bigquery.Client | None = None):
        self.client = client or get_bigquery_client()
        self.dataset_id = settings.BIGQUERY_DATASET
        self.table_id = settings.BIGQUERY_TABLE
        self.table_ref = (
            f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.table_id}"
        )
        self.cost_attribution_table_id = settings.BIGQUERY_COST_ATTRIBUTION_TABLE
        self.cost_attribution_table_ref = f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.cost_attribution_table_id}"
        self.agent_telemetry_table_id = settings.BIGQUERY_AGENT_TELEMETRY_TABLE
        self.agent_telemetry_table_ref = f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.agent_telemetry_table_id}"
        self.user_feedback_table_id = settings.BIGQUERY_USER_FEEDBACK_TABLE
        self.user_feedback_table_ref = f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.user_feedback_table_id}"

    def _execute_query(
        self,
        query: str,
        query_parameters: list[bigquery.ScalarQueryParameter],
        operation_name: str,
    ) -> Any:
        try:
            job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)
            query_job = self.client.query(query, job_config=job_config)
            return query_job.result()
        except GoogleCloudError as e:
            logger.error(f"Google Cloud error {operation_name}: {e}")
            raise DatabaseError(f"Failed to {operation_name}: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error {operation_name}: {e}")
            raise DatabaseError(f"Unexpected error {operation_name}: {e}") from e

    def insert_cost_attribution(
        self,
        job_id: str,
        username: str | None = None,
        email: str | None = None,
        business_unit: str | None = None,
        model_version: str | None = None,
        temperature: float | None = None,
        prompt_template_version: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        search_count: int | None = None,
        search_cost_usd: float | None = None,
        token_cost_usd: float | None = None,
        total_cost_usd: float | None = None,
        latency_seconds: float | None = None,
        cost_usd: float | None = None,
    ) -> bool:
        """Insert a cost attribution record with search cost breakdown"""
        if self.client is None:
            return True
        now = datetime.now(UTC)
        query = f"""
        INSERT INTO `{self.cost_attribution_table_ref}` (
            job_execution_id, username, email, business_unit, model_version, temperature, prompt_template_version,
            input_tokens, output_tokens, total_tokens, search_count, search_cost_usd, token_cost_usd, total_cost_usd,
            latency_seconds, cost_usd, created_at
        )
        VALUES (
            @job_execution_id, @username, @email, @business_unit, @model_version, @temperature, @prompt_template_version,
            @input_tokens, @output_tokens, @total_tokens, @search_count, @search_cost_usd, @token_cost_usd, @total_cost_usd,
            @latency_seconds, @cost_usd, @created_at
        )
        """

        query_parameters = [
            bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("username", "STRING", username),
            bigquery.ScalarQueryParameter("email", "STRING", email),
            bigquery.ScalarQueryParameter("business_unit", "STRING", business_unit),
            bigquery.ScalarQueryParameter("model_version", "STRING", model_version),
            bigquery.ScalarQueryParameter("temperature", "FLOAT64", temperature),
            bigquery.ScalarQueryParameter(
                "prompt_template_version", "STRING", prompt_template_version
            ),
            bigquery.ScalarQueryParameter("input_tokens", "INT64", input_tokens),
            bigquery.ScalarQueryParameter("output_tokens", "INT64", output_tokens),
            bigquery.ScalarQueryParameter("total_tokens", "INT64", total_tokens),
            bigquery.ScalarQueryParameter("search_count", "INT64", search_count),
            bigquery.ScalarQueryParameter(
                "search_cost_usd", "FLOAT64", search_cost_usd
            ),
            bigquery.ScalarQueryParameter("token_cost_usd", "FLOAT64", token_cost_usd),
            bigquery.ScalarQueryParameter("total_cost_usd", "FLOAT64", total_cost_usd),
            bigquery.ScalarQueryParameter(
                "latency_seconds", "FLOAT64", latency_seconds
            ),
            bigquery.ScalarQueryParameter("cost_usd", "FLOAT64", cost_usd),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", now),
        ]

        self._execute_query(query, query_parameters, "inserting cost attribution")
        logger.info(
            f"Inserted cost attribution for job {job_id} (searches: {search_count})"
        )
        return True

    def insert_user_feedback(
        self,
        job_id: str,
        user_email: str,
        feedback: str | None = None,
    ) -> bool:
        """Insert user feedback into the user_feedback table"""
        if self.client is None:
            logger.info(
                f"Local Bypass: Inserted feedback for job {job_id} from {user_email}"
            )
            return True

        query = f"""
        INSERT INTO `{self.user_feedback_table_ref}` (job_id, user_email, feedback)
        VALUES (@job_id, @user_email, @feedback)
        """

        query_parameters = [
            bigquery.ScalarQueryParameter("job_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("user_email", "STRING", user_email),
            bigquery.ScalarQueryParameter("feedback", "STRING", feedback),
        ]

        self._execute_query(query, query_parameters, "inserting user feedback")
        logger.info(f"Inserted user feedback for job {job_id}")
        return True

    def create_request(
        self, job_id: str, company_name: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        """Create a new research request record"""
        if self.client is None:
            logger.info(f"Local Bypass: Recorded job {job_id} for '{company_name}'")
            return True
        now = datetime.now(UTC)
        query = f"""
        INSERT INTO `{self.table_ref}` (
            job_execution_id,
            company_name,
            status,
            created_at,
            updated_at,
            gcs_uri,
            error_message,
            metadata,
            progress,
            current_step
        )
        VALUES (
            @job_execution_id,
            @company_name,
            @status,
            @created_at,
            @updated_at,
            @gcs_uri,
            @error_message,
            @metadata,
            @progress,
            @current_step
        )
        """

        query_parameters = [
            bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("company_name", "STRING", company_name),
            bigquery.ScalarQueryParameter("status", "STRING", "QUEUED"),
            bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", now),
            bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", now),
            bigquery.ScalarQueryParameter("gcs_uri", "STRING", None),
            bigquery.ScalarQueryParameter("error_message", "STRING", None),
            bigquery.ScalarQueryParameter(
                "metadata",
                "JSON",
                json.dumps(metadata) if metadata else None,
            ),
            bigquery.ScalarQueryParameter("progress", "INT64", 0),
            bigquery.ScalarQueryParameter("current_step", "STRING", None),
        ]

        self._execute_query(query, query_parameters, "creating request")

        logger.info(
            f"Created job {job_id} for company '{company_name}' with status PENDING"
        )
        return True

    def update_status(
        self,
        job_id: str,
        status: str | None,
        gcs_uri: str | None = None,
        error: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        metadata_update: dict | None = None,
    ) -> bool:
        """Update job status fields"""
        if self.client is None:
            return True
        now = datetime.now(UTC)

        update_fields = ["updated_at = @updated_at"]
        query_params = [
            bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id),
            bigquery.ScalarQueryParameter("updated_at", "TIMESTAMP", now),
        ]

        if status is not None:
            update_fields.append("status = @status")
            query_params.append(
                bigquery.ScalarQueryParameter("status", "STRING", status)
            )

        if gcs_uri is not None:
            update_fields.append("gcs_uri = @gcs_uri")
            query_params.append(
                bigquery.ScalarQueryParameter("gcs_uri", "STRING", gcs_uri)
            )

        if error is not None:
            update_fields.append("error_message = @error_message")
            query_params.append(
                bigquery.ScalarQueryParameter("error_message", "STRING", error)
            )

        if progress is not None:
            update_fields.append("progress = @progress")
            query_params.append(
                bigquery.ScalarQueryParameter("progress", "INT64", progress)
            )

        if current_step is not None:
            update_fields.append("current_step = @current_step")
            query_params.append(
                bigquery.ScalarQueryParameter("current_step", "STRING", current_step)
            )

        if metadata_update is not None:
            merged = {**self._get_metadata_dict(job_id), **metadata_update}
            update_fields.append("metadata = PARSE_JSON(@metadata)")
            query_params.append(
                bigquery.ScalarQueryParameter("metadata", "STRING", json.dumps(merged))
            )

        query = f"""
        UPDATE `{self.table_ref}`
        SET {", ".join(update_fields)}
        WHERE job_execution_id = @job_execution_id
        """

        self._execute_query(query, query_params, "updating status")

        fields_updated = []
        if status is not None:
            fields_updated.append(f"status={status}")
        if progress is not None:
            fields_updated.append(f"progress={progress}")
        if current_step is not None:
            fields_updated.append(f"current_step={current_step!r}")
        logger.info(
            f"Updated [{', '.join(fields_updated) or 'updated_at'}] for job {job_id}"
        )
        return True

    def _parse_metadata_row(self, raw: Any) -> dict[str, Any]:
        if not raw:
            return {}
        if isinstance(raw, dict):
            return dict(raw)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                return {}
        return {}

    def _get_metadata_dict(self, job_id: str) -> dict[str, Any]:
        """Load existing metadata JSON for merge (avoids JSON_MERGE_PATCH)."""
        if self.client is None:
            return {}
        query = f"""
        SELECT metadata
        FROM `{self.table_ref}`
        WHERE job_execution_id = @job_execution_id
        LIMIT 1
        """
        query_parameters = [
            bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id)
        ]
        results = list(self._execute_query(query, query_parameters, "getting metadata"))
        if not results:
            return {}
        return self._parse_metadata_row(getattr(results[0], "metadata", None))

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        """Get the latest status for a job"""
        if self.client is None:
            return {
                "request_id": job_id,
                "status": "PROCESSING",
                "progress": 50,
                "current_step": "Local Step",
                "current_agent": None,
            }
        query = f"""
        SELECT status, updated_at, company_name, progress, current_step, metadata
        FROM `{self.table_ref}`
        WHERE job_execution_id = @job_execution_id
        ORDER BY updated_at DESC
        LIMIT 1
        """

        query_parameters = [
            bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id)
        ]

        results = list(self._execute_query(query, query_parameters, "getting status"))

        if not results:
            return None

        row = results[0]

        metadata_dict = self._parse_metadata_row(getattr(row, "metadata", None))

        return {
            "request_id": job_id,
            "company_name": row.company_name,
            "status": row.status,
            "progress": row.progress if row.progress is not None else 0,
            "current_step": row.current_step,
            "current_agent": metadata_dict.get("current_agent"),
        }

    def get_request_result(
        self,
        job_id: str,
        gcs_repository: Any | None = None,
    ) -> dict[str, Any] | None:
        """Get the complete result for a job"""
        if self.client is None:
            gcs_repo = gcs_repository
            if gcs_repo is None:
                from src.shared.repositories.gcs_repository import GCSRepository

                gcs_repo = GCSRepository()
            content = gcs_repo.download_markdown(job_id)
            if content:
                return {
                    "request_id": job_id,
                    "status": "COMPLETED",
                    "metadata": {},
                    "download_url": job_id,
                    "report_content": content,
                }
            return None

        query = f"""
        SELECT
            job_execution_id,
            company_name,
            status,
            gcs_uri,
            error_message,
            metadata,
            updated_at
        FROM `{self.table_ref}`
        WHERE job_execution_id = @job_execution_id
        ORDER BY updated_at DESC
        LIMIT 1
        """

        query_parameters = [
            bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id)
        ]

        results = list(
            self._execute_query(query, query_parameters, "getting request result")
        )

        if not results:
            return None

        row = results[0]

        metadata = self._parse_metadata_row(getattr(row, "metadata", None))

        result = {
            "request_id": job_id,
            "status": row.status,
            "metadata": metadata,
        }

        # Add GCS download URLs if available
        if row.gcs_uri and row.status == "COMPLETED":
            gcs_repo = gcs_repository
            if gcs_repo is None:
                from src.shared.repositories.gcs_repository import GCSRepository

                gcs_repo = GCSRepository()
            result["download_url"] = gcs_repo.get_signed_url(row.gcs_uri)
            result["report_content"] = gcs_repo.download_markdown(job_id)

        return result

    def insert_agent_telemetry_batch(self, records: list[dict[str, Any]]) -> bool:
        """Insert telemetry records in batch"""
        if self.client is None:
            return True
        if not records:
            return True
        try:
            errors = self.client.insert_rows_json(
                self.agent_telemetry_table_ref, records
            )
            if errors:
                raise DatabaseError(f"Failed to insert telemetry rows: {errors}")
            logger.info(
                f"Inserted {len(records)} agent telemetry records into {self.agent_telemetry_table_ref}"
            )
            return True
        except GoogleCloudError as e:
            logger.error(f"Google Cloud error inserting agent telemetry batch: {e}")
            raise DatabaseError(f"Failed to insert agent telemetry batch: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error inserting agent telemetry batch: {e}")
            if isinstance(e, DatabaseError):
                raise
            raise DatabaseError(
                f"Unexpected error inserting agent telemetry batch: {e}"
            ) from e

    def get_requests_by_status(
        self, status: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get jobs by status"""
        if self.client is None:
            return []
        query = f"""
        SELECT DISTINCT job_execution_id, company_name, updated_at
        FROM (
            SELECT
                job_execution_id,
                company_name,
                status,
                updated_at,
                ROW_NUMBER() OVER (PARTITION BY job_execution_id ORDER BY updated_at DESC) as rn
            FROM `{self.table_ref}`
        )
        WHERE rn = 1 AND status = @status
        ORDER BY updated_at DESC
        LIMIT @limit
        """

        query_parameters = [
            bigquery.ScalarQueryParameter("status", "STRING", status),
            bigquery.ScalarQueryParameter("limit", "INT64", limit),
        ]

        results = []
        for row in self._execute_query(
            query, query_parameters, "getting requests by status"
        ):
            results.append(
                {
                    "job_execution_id": row.job_execution_id,
                    "company_name": row.company_name,
                    "updated_at": row.updated_at.isoformat()
                    if row.updated_at
                    else None,
                }
            )

        return results
