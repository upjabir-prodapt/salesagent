"""Search result caching and retrieval service (Firestore-backed)."""

from __future__ import annotations

import json
from typing import Any

from src.core.logging_config import logger
from src.repositories.firestore_repository import (
    FirestoreSearchCacheRepository,
    query_hash,
)


class SearchCacheService:
    """Cache search results and queries for companies in Firestore."""

    def __init__(self, repository: FirestoreSearchCacheRepository | None = None):
        self.repository = repository or FirestoreSearchCacheRepository()

    def _query_hash(self, query: str) -> str:
        """Generate hash for query deduplication."""
        return query_hash(query)

    def get_cached_searches(self, company_name: str) -> dict[str, Any] | None:
        """Fetch cached search results for a company."""
        documents = self.repository.get_searches_for_company(company_name)
        if not documents:
            return None

        cached: dict[str, Any] = {}
        for doc in documents:
            query_str = doc.get("query")
            if not query_str:
                continue

            results_json = doc.get("search_results")
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
                "domain": doc.get("domain") or "unknown",
                "cached_at": doc.get("search_date"),
            }

        logger.info(f"Retrieved {len(cached)} cached searches for {company_name}")
        return cached or None

    def cache_search_results(
        self,
        company_name: str,
        query: str,
        search_results: dict[str, Any],
        domain: str | None = None,
    ) -> bool:
        """Cache search results for a query."""
        try:
            self.repository.insert_search_query(
                company_name=company_name,
                query=query,
                search_results=json.dumps(search_results),
                domain=domain,
            )
            logger.debug(f"Cached search result for: {query}")
            return True
        except Exception as e:
            logger.warning(f"Failed to cache search result: {e}")
            return False

    def get_search_count(self, company_name: str) -> int:
        """Get total search count for a company."""
        return self.repository.count_searches(company_name)

    def get_uncached_queries(
        self, company_name: str, proposed_queries: list[str]
    ) -> list[str]:
        """Return only queries not already cached."""
        if not proposed_queries:
            return []

        hashes = [self._query_hash(q) for q in proposed_queries]
        cached_hashes = self.repository.get_cached_query_hashes(company_name, hashes)

        uncached = [
            q
            for q, qh in zip(proposed_queries, hashes, strict=False)
            if qh not in cached_hashes
        ]

        logger.info(
            f"Found {len(uncached)} uncached queries out of {len(proposed_queries)}"
        )
        return uncached
