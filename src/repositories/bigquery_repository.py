import json
from datetime import UTC, datetime
from typing import Any

from google.cloud import bigquery
from google.cloud.exceptions import GoogleCloudError, NotFound
from loguru import logger

from ..core.clients import client_pool
from ..core.config import settings
from ..core.exceptions import DatabaseError


class BigQueryRepository:
    """Repository for BigQuery operations with local bypass support"""

    def __init__(self, client: bigquery.Client = None):
        self.client = client or client_pool.get_bq_client()
        self.dataset_id = settings.BIGQUERY_DATASET
        self.table_id = settings.BIGQUERY_TABLE
        self.table_ref = (
            f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.table_id}"
        )
        self.cost_attribution_table_id = settings.BIGQUERY_COST_ATTRIBUTION_TABLE
        self.cost_attribution_table_ref = f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.cost_attribution_table_id}"
        self.agent_telemetry_table_id = settings.BIGQUERY_AGENT_TELEMETRY_TABLE
        self.agent_telemetry_table_ref = f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.agent_telemetry_table_id}"
        self.users_table_id = settings.BIGQUERY_USERS_TABLE
        self.users_table_ref = f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.users_table_id}"

    def ensure_table_exists(self) -> bool:
        """Ensure the main requests table exists in BigQuery"""
        if self.client is None:
            logger.warning("BigQuery client is None; skipping ensure_table_exists")
            return True
        try:
            # 1. Check if table already exists
            try:
                self.client.get_table(self.table_ref)
                logger.info(f"BigQuery table already exists: {self.table_ref}")
                return True
            except NotFound:
                # Table doesn't exist, proceed to create it
                pass

            # 2. Check/Create dataset
            dataset_ref = f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}"
            try:
                self.client.get_dataset(dataset_ref)
                logger.info(f"BigQuery dataset already exists: {dataset_ref}")
            except NotFound:
                logger.info(f"Creating BigQuery dataset: {dataset_ref}")
                dataset = bigquery.Dataset(dataset_ref)
                dataset.location = settings.GOOGLE_CLOUD_LOCATION
                self.client.create_dataset(dataset, timeout=30)
                logger.info(f"Created BigQuery dataset: {dataset_ref}")

            # 3. Define schema
            schema = [
                bigquery.SchemaField("job_execution_id", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("company_name", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("status", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
                bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
                bigquery.SchemaField("gcs_uri", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("error_message", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("metadata", "JSON", mode="NULLABLE"),
                bigquery.SchemaField("progress", "INT64", mode="NULLABLE"),
                bigquery.SchemaField("current_step", "STRING", mode="NULLABLE"),
            ]

            # 4. Create table
            table = bigquery.Table(self.table_ref, schema=schema)
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="created_at",
            )
            self.client.create_table(table)

            logger.info(f"Created BigQuery table: {self.table_ref}")
            logger.info(f"Table schema: {[field.name for field in schema]}")
            return True

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error creating table: {e}")
            raise DatabaseError(f"Failed to create BigQuery table: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error creating table: {e}")
            raise DatabaseError(f"Unexpected error creating BigQuery table: {e}") from e

    def ensure_cost_attribution_table_exists(self) -> bool:
        """Ensure the cost attribution table exists"""
        if self.client is None:
            return True
        try:
            try:
                self.client.get_table(self.cost_attribution_table_ref)
                logger.info(
                    f"BigQuery cost attribution table already exists: {self.cost_attribution_table_ref}"
                )
                return True
            except NotFound:
                pass

            schema = [
                bigquery.SchemaField("job_execution_id", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("model_version", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("temperature", "FLOAT64", mode="NULLABLE"),
                bigquery.SchemaField(
                    "prompt_template_version", "STRING", mode="NULLABLE"
                ),
                bigquery.SchemaField("input_tokens", "INT64", mode="NULLABLE"),
                bigquery.SchemaField("output_tokens", "INT64", mode="NULLABLE"),
                bigquery.SchemaField("total_tokens", "INT64", mode="NULLABLE"),
                bigquery.SchemaField("latency_seconds", "FLOAT64", mode="NULLABLE"),
                bigquery.SchemaField("source_domains", "JSON", mode="NULLABLE"),
                bigquery.SchemaField("cost_usd", "FLOAT64", mode="NULLABLE"),
                bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
            ]

            table = bigquery.Table(self.cost_attribution_table_ref, schema=schema)
            table.time_partitioning = bigquery.TimePartitioning(
                type_=bigquery.TimePartitioningType.DAY,
                field="created_at",
            )
            self.client.create_table(table)
            logger.info(
                f"Created BigQuery cost attribution table: {self.cost_attribution_table_ref}"
            )
            return True

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error creating cost attribution table: {e}")
            raise DatabaseError(f"Failed to create cost attribution table: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error creating cost attribution table: {e}")
            raise DatabaseError(
                f"Unexpected error creating cost attribution table: {e}"
            ) from e

    def insert_cost_attribution(
        self,
        job_id: str,
        model_version: str | None = None,
        temperature: float | None = None,
        prompt_template_version: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
        latency_seconds: float | None = None,
        source_domains: list[str] | None = None,
        cost_usd: float | None = None,
    ) -> bool:
        """Insert a cost attribution record"""
        if self.client is None:
            return True
        try:
            now = datetime.now(UTC)
            query = f"""
            INSERT INTO `{self.cost_attribution_table_ref}` (
                job_execution_id, model_version, temperature, prompt_template_version,
                input_tokens, output_tokens, total_tokens, latency_seconds,
                source_domains, cost_usd, created_at
            )
            VALUES (
                @job_execution_id, @model_version, @temperature, @prompt_template_version,
                @input_tokens, @output_tokens, @total_tokens, @latency_seconds,
                @source_domains, @cost_usd, @created_at
            )
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id),
                    bigquery.ScalarQueryParameter(
                        "model_version", "STRING", model_version
                    ),
                    bigquery.ScalarQueryParameter(
                        "temperature", "FLOAT64", temperature
                    ),
                    bigquery.ScalarQueryParameter(
                        "prompt_template_version", "STRING", prompt_template_version
                    ),
                    bigquery.ScalarQueryParameter(
                        "input_tokens", "INT64", input_tokens
                    ),
                    bigquery.ScalarQueryParameter(
                        "output_tokens", "INT64", output_tokens
                    ),
                    bigquery.ScalarQueryParameter(
                        "total_tokens", "INT64", total_tokens
                    ),
                    bigquery.ScalarQueryParameter(
                        "latency_seconds", "FLOAT64", latency_seconds
                    ),
                    bigquery.ScalarQueryParameter(
                        "source_domains",
                        "JSON",
                        json.dumps(source_domains)
                        if source_domains is not None
                        else None,
                    ),
                    bigquery.ScalarQueryParameter("cost_usd", "FLOAT64", cost_usd),
                    bigquery.ScalarQueryParameter("created_at", "TIMESTAMP", now),
                ]
            )

            query_job = self.client.query(query, job_config=job_config)
            query_job.result()
            logger.info(f"Inserted cost attribution for job {job_id}")
            return True

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error inserting cost attribution: {e}")
            raise DatabaseError(f"Failed to insert cost attribution: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error inserting cost attribution: {e}")
            raise DatabaseError(f"Unexpected error inserting cost attribution: {e}") from e

    def create_request(
        self, job_id: str, company_name: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        """Create a new research request record"""
        if self.client is None:
            logger.info(f"Local Bypass: Recorded job {job_id} for '{company_name}'")
            return True
        try:
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

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id),
                    bigquery.ScalarQueryParameter(
                        "company_name", "STRING", company_name
                    ),
                    bigquery.ScalarQueryParameter("status", "STRING", "PENDING"),
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
            )

            query_job = self.client.query(query, job_config=job_config)
            query_job.result()

            logger.info(
                f"Created job {job_id} for company '{company_name}' with status PENDING"
            )
            return True

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error creating request: {e}")
            raise DatabaseError(f"Database operation failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error creating request: {e}")
            raise DatabaseError(f"Unexpected database error: {e}") from e

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
        try:
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
                    bigquery.ScalarQueryParameter(
                        "current_step", "STRING", current_step
                    )
                )

            if metadata_update is not None:
                update_fields.append("metadata = @metadata_patch")
                query_params.append(
                    bigquery.ScalarQueryParameter(
                        "metadata_patch", "JSON", json.dumps(metadata_update)
                    )
                )

            query = f"""
            UPDATE `{self.table_ref}`
            SET {", ".join(update_fields)}
            WHERE job_execution_id = @job_execution_id
            """

            job_config = bigquery.QueryJobConfig(query_parameters=query_params)
            query_job = self.client.query(query, job_config=job_config)
            query_job.result()

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

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error updating status: {e}")
            raise DatabaseError(f"Database operation failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error updating status: {e}")
            raise DatabaseError(f"Unexpected database error: {e}") from e

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        """Get the latest status for a job"""
        if self.client is None:
            return {
                "request_id": job_id,
                "status": "PROCESSING",
                "progress": 50,
                "current_step": "Local Step",
            }
        try:
            query = f"""
            SELECT status, updated_at, company_name, progress, current_step
            FROM `{self.table_ref}`
            WHERE job_execution_id = @job_execution_id
            ORDER BY updated_at DESC
            LIMIT 1
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id)
                ]
            )

            query_job = self.client.query(query, job_config=job_config)
            results = list(query_job.result())

            if not results:
                return None

            row = results[0]
            return {
                "request_id": job_id,
                "status": row.status,
                "progress": row.progress if row.progress is not None else 0,
                "current_step": row.current_step,
            }

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error getting status: {e}")
            raise DatabaseError(f"Database query failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error getting status: {e}")
            raise DatabaseError(f"Unexpected database error: {e}") from e

    def get_request_result(self, job_id: str) -> dict[str, Any] | None:
        """Get the complete result for a job"""
        if self.client is None:
            from ..repositories.gcs_repository import GCSRepository
            gcs_repo = GCSRepository()
            content = gcs_repo.download_markdown(job_id)
            if content:
                return {
                    "request_id": job_id,
                    "status": "COMPLETED",
                    "metadata": {},
                    "download_url": job_id,
                    "report_content": content
                }
            return None
            
        try:
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

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("job_execution_id", "STRING", job_id)
                ]
            )

            query_job = self.client.query(query, job_config=job_config)
            results = list(query_job.result())

            if not results:
                return None

            row = results[0]

            # Parse metadata JSON
            metadata = {}
            if row.metadata:
                try:
                    metadata = json.loads(row.metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            result = {
                "request_id": job_id,
                "status": row.status,
                "metadata": metadata,
            }

            # Add GCS download URLs if available
            if row.gcs_uri and row.status == "COMPLETED":
                from ..repositories.gcs_repository import GCSRepository

                gcs_repo = GCSRepository()
                result["download_url"] = gcs_repo.get_signed_url(row.gcs_uri)
                result["report_content"] = gcs_repo.download_markdown(job_id)

            return result

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error getting result: {e}")
            raise DatabaseError(f"Database query failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error getting result: {e}")
            raise DatabaseError(f"Unexpected database error: {e}") from e

    def ensure_agent_telemetry_table_exists(self) -> bool:
        """Ensure the agent telemetry table exists"""
        if self.client is None:
            return True
        return True

    def insert_agent_telemetry_batch(self, records: list[dict[str, Any]]) -> bool:
        """Insert telemetry records in batch"""
        if self.client is None:
            return True
        return True

    def get_requests_by_status(
        self, status: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Get jobs by status"""
        if self.client is None:
            return []
        try:
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

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("status", "STRING", status),
                    bigquery.ScalarQueryParameter("limit", "INT64", limit),
                ]
            )

            query_job = self.client.query(query, job_config=job_config)
            results = []

            for row in query_job.result():
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

        except GoogleCloudError as e:
            logger.error(f"Google Cloud error getting requests by status: {e}")
            raise DatabaseError(f"Database query failed: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error getting requests by status: {e}")
            raise DatabaseError(f"Unexpected database error: {e}") from e

    def ensure_users_table_exists(self) -> bool:
        """Ensure the users table exists in BigQuery"""
        if self.client is None:
            return True
        try:
            try:
                self.client.get_table(self.users_table_ref)
                logger.info(f"BigQuery users table already exists: {self.users_table_ref}")
                return True
            except NotFound:
                pass

            schema = [
                bigquery.SchemaField("email", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("business_unit", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("organization", "STRING", mode="REQUIRED"),
                bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
            ]

            table = bigquery.Table(self.users_table_ref, schema=schema)
            self.client.create_table(table)
            logger.info(f"Created BigQuery users table: {self.users_table_ref}")
            return True
        except GoogleCloudError as e:
            logger.error(f"Google Cloud error creating users table: {e}")
            raise DatabaseError(f"Failed to create users table: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error creating users table: {e}")
            raise DatabaseError(f"Unexpected error creating users table: {e}") from e

    def verify_user(self, email: str, business_unit: str, organization: str) -> dict[str, Any] | None:
        """Verify user details against BigQuery"""
        if self.client is None:
            # Local bypass for testing
            return {
                "email": email,
                "business_unit": business_unit,
                "organization": organization
            }
        try:
            query = f"""
            SELECT email, business_unit, organization
            FROM `{self.users_table_ref}`
            WHERE email = @email 
              AND business_unit = @business_unit 
              AND organization = @organization
            LIMIT 1
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("email", "STRING", email),
                    bigquery.ScalarQueryParameter("business_unit", "STRING", business_unit),
                    bigquery.ScalarQueryParameter("organization", "STRING", organization),
                ]
            )
            query_job = self.client.query(query, job_config=job_config)
            results = list(query_job.result())

            if not results:
                return None

            row = results[0]
            return {
                "email": row.email,
                "business_unit": row.business_unit,
                "organization": row.organization
            }
        except Exception as e:
            logger.error(f"Error verifying user {email}: {e}")
            return None
