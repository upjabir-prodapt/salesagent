"""Search result caching and retrieval service."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from google.cloud import bigquery
from google.cloud.exceptions import GoogleCloudError

from src.core.config import settings
from src.core.exceptions import DatabaseError
from src.core.logging_config import logger
from src.repositories.bigquery_repository import BigQueryRepository


class SearchCacheService:
    """Cache search results and queries for companies."""

    def __init__(self, bq_repo: BigQueryRepository | None = None):
        self.bq_repo = bq_repo or BigQueryRepository()
        self.client = self.bq_repo.client
        self.dataset_id = settings.BIGQUERY_DATASET
        self.cache_table_id = "search_cache"
        self.cache_table_ref = (
            f"{settings.GOOGLE_CLOUD_PROJECT}.{self.dataset_id}.{self.cache_table_id}"
        )

    def _query_hash(self, query: str) -> str:
        """Generate hash for query deduplication."""
        return hashlib.sha256(query.lower().encode()).hexdigest()[:16]

    def get_cached_searches(self, company_name: str) -> dict[str, Any] | None:
        """Fetch cached search results for a company."""
        if self.client is None:
            return None

        try:
            query = f"""
            SELECT
                query,
                search_results,
                domain,
                search_date,
                query_hash
            FROM `{self.cache_table_ref}`
            WHERE company_name = @company_name
            ORDER BY search_date DESC
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("company_name", "STRING", company_name)
                ]
            )

            query_job = self.client.query(query, job_config=job_config)
            results = list(query_job.result())

            if not results:
                return None

            cached = {}
            for row in results:
                query_str = row.query
                domain = row.domain or "unknown"
                results_json = getattr(row, "search_results", None)

                if results_json:
                    try:
                        search_results = (
                            json.loads(results_json)
                            if isinstance(results_json, str)
                            else results_json
                        )
                    except json.JSONDecodeError:
                        search_results = {}
                else:
                    search_results = {}

                cached[query_str] = {
                    "results": search_results,
                    "domain": domain,
                    "cached_at": row.search_date,
                }

            logger.info(
                f"Retrieved {len(cached)} cached searches for {company_name}"
            )
            return cached
        except GoogleCloudError as e:
            logger.warning(f"Failed to retrieve cached searches: {e}")
            return None

    def cache_search_results(
        self,
        company_name: str,
        query: str,
        search_results: dict[str, Any],
        domain: str | None = None,
    ) -> bool:
        """Cache search results for a query."""
        if self.client is None:
            logger.info(f"Local bypass: caching search for {company_name}: {query}")
            return True

        try:
            now = datetime.now(UTC)
            query_hash = self._query_hash(query)

            insert_query = f"""
            INSERT INTO `{self.cache_table_ref}` (
                company_name,
                query,
                query_hash,
                search_results,
                domain,
                search_date
            )
            VALUES (
                @company_name,
                @query,
                @query_hash,
                @search_results,
                @domain,
                @search_date
            )
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("company_name", "STRING", company_name),
                    bigquery.ScalarQueryParameter("query", "STRING", query),
                    bigquery.ScalarQueryParameter("query_hash", "STRING", query_hash),
                    bigquery.ScalarQueryParameter(
                        "search_results", "STRING", json.dumps(search_results)
                    ),
                    bigquery.ScalarQueryParameter("domain", "STRING", domain),
                    bigquery.ScalarQueryParameter("search_date", "TIMESTAMP", now),
                ]
            )

            self.client.query(insert_query, job_config=job_config).result()
            logger.debug(f"Cached search result for: {query}")
            return True
        except GoogleCloudError as e:
            logger.warning(f"Failed to cache search result: {e}")
            return False

    def get_search_count(self, company_name: str) -> int:
        """Get total search count for a company."""
        if self.client is None:
            return 0

        try:
            query = f"""
            SELECT COUNT(*) as count
            FROM `{self.cache_table_ref}`
            WHERE company_name = @company_name
            """

            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("company_name", "STRING", company_name)
                ]
            )

            query_job = self.client.query(query, job_config=job_config)
            result = list(query_job.result())[0]
            return result.count
        except GoogleCloudError as e:
            logger.warning(f"Failed to get search count: {e}")
            return 0

    def get_uncached_queries(
        self, company_name: str, proposed_queries: list[str]
    ) -> list[str]:
        """Return only queries not already cached."""
        if self.client is None:
            return proposed_queries

        try:
            # Get all cached query hashes
            query_hashes = [self._query_hash(q) for q in proposed_queries]

            if not query_hashes:
                return []

            placeholders = ",".join(["@hash_" + str(i) for i in range(len(query_hashes))])

            query = f"""
            SELECT DISTINCT query_hash
            FROM `{self.cache_table_ref}`
            WHERE company_name = @company_name AND query_hash IN ({placeholders})
            """

            query_params = [
                bigquery.ScalarQueryParameter("company_name", "STRING", company_name),
            ]

            for i, qh in enumerate(query_hashes):
                query_params.append(
                    bigquery.ScalarQueryParameter(f"hash_{i}", "STRING", qh)
                )

            job_config = bigquery.QueryJobConfig(query_parameters=query_params)
            query_job = self.client.query(query, job_config=job_config)
            results = list(query_job.result())

            cached_hashes = {row.query_hash for row in results}
            uncached = [
                q
                for q, qh in zip(proposed_queries, query_hashes)
                if qh not in cached_hashes
            ]

            logger.info(
                f"Found {len(uncached)} uncached queries out of {len(proposed_queries)}"
            )
            return uncached
        except GoogleCloudError as e:
            logger.warning(f"Failed to check cached queries: {e}")
            return proposed_queries
