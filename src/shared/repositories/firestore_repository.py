"""Firestore repository for the search query cache.

Replaces the former BigQuery ``search_cache`` table. Every executed web search
is stored as one document in the ``search_cache`` collection, keyed by
``<company_key>__<query_hash>`` so repeated runs upsert instead of duplicating.

Document shape::

    company_name    str       company name as supplied by the caller
    company_key     str       normalised lookup key (sha256 prefix, lowercased)
    query           str       the executed search query
    query_hash      str       sha256 prefix of the lowercased query
    search_results  str       JSON-encoded result payload (index-exempt)
    domain          str       research domain / agent that issued the search
    search_date     datetime  execution timestamp (UTC)

``search_results`` must be exempt from single-field indexing: payloads exceed
Firestore's 1500-byte indexed value limit. ``scripts/create_firestore_db.sh``
provisions that exemption along with the composite indexes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from google.api_core.exceptions import GoogleAPICallError
from google.cloud import firestore  # type: ignore

from src.shared.config import settings
from src.shared.exceptions import DatabaseError
from src.shared.logging_config import logger

from .clients import get_firestore_client

# Firestore caps ``IN`` filters at 30 values per query.
_IN_FILTER_CHUNK = 30
# Firestore caps a batched write at 500 operations.
_BATCH_LIMIT = 500


def company_key(company_name: str) -> str:
    """Stable lookup key for a company name (case/whitespace insensitive)."""
    normalized = (company_name or "").strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def query_hash(query: str) -> str:
    """Stable short hash used for query deduplication."""
    return hashlib.sha256((query or "").lower().encode()).hexdigest()[:16]


def _coerce_datetime(value: Any) -> datetime:
    """Accept ISO strings (session state) or datetimes; return an aware UTC dt."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


class FirestoreSearchCacheRepository:
    """Repository for search cache documents in Firestore."""

    def __init__(self, client: firestore.Client | None = None):
        self.client = client or get_firestore_client()
        self.collection_name = settings.FIRESTORE_SEARCH_CACHE_COLLECTION

    @property
    def collection(self) -> Any:
        return self.client.collection(self.collection_name)

    def _document_id(self, company_name: str, qhash: str) -> str:
        return f"{company_key(company_name)}__{qhash}"

    def _to_document(self, record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Normalise a search log row into a Firestore document."""
        company_name = record.get("company_name") or "unknown"
        query = record.get("query") or ""
        qhash = record.get("query_hash") or query_hash(query)
        results = record.get("search_results")
        if not isinstance(results, str):
            results = json.dumps(results if results is not None else [])

        document = {
            "company_name": company_name,
            "company_key": company_key(company_name),
            "query": query,
            "query_hash": qhash,
            "search_results": results,
            "domain": record.get("domain"),
            "search_date": _coerce_datetime(record.get("search_date")),
        }
        return self._document_id(company_name, qhash), document

    def insert_search_query_batch(self, records: list[dict[str, Any]]) -> bool:
        """Upsert executed search queries into the search cache collection."""
        if self.client is None:
            return True
        if not records:
            return True
        try:
            written = 0
            for start in range(0, len(records), _BATCH_LIMIT):
                batch = self.client.batch()
                for record in records[start : start + _BATCH_LIMIT]:
                    doc_id, document = self._to_document(record)
                    batch.set(self.collection.document(doc_id), document)
                    written += 1
                batch.commit()
            logger.info(
                f"Upserted {written} search query documents into {self.collection_name}"
            )
            return True
        except GoogleAPICallError as e:
            logger.error(f"Google Cloud error inserting search query batch: {e}")
            raise DatabaseError(f"Failed to insert search query batch: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error inserting search query batch: {e}")
            if isinstance(e, DatabaseError):
                raise
            raise DatabaseError(
                f"Unexpected error inserting search query batch: {e}"
            ) from e

    def insert_search_query(
        self,
        company_name: str,
        query: str,
        search_results: Any,
        domain: str | None = None,
        search_date: datetime | None = None,
    ) -> bool:
        """Upsert a single executed search query."""
        return self.insert_search_query_batch(
            [
                {
                    "company_name": company_name,
                    "query": query,
                    "query_hash": query_hash(query),
                    "search_results": search_results,
                    "domain": domain,
                    "search_date": search_date or datetime.now(UTC),
                }
            ]
        )

    def get_searches_for_company(self, company_name: str) -> list[dict[str, Any]]:
        """All cached search documents for a company, newest first."""
        if self.client is None:
            return []
        try:
            docs = (
                self.collection.where(
                    filter=firestore.FieldFilter(
                        "company_key", "==", company_key(company_name)
                    )
                )
                .order_by("search_date", direction=firestore.Query.DESCENDING)
                .stream()
            )
            return [doc.to_dict() for doc in docs]
        except GoogleAPICallError as e:
            logger.warning(f"Failed to retrieve cached searches: {e}")
            return []

    def count_searches(self, company_name: str) -> int:
        """Number of cached searches for a company."""
        if self.client is None:
            return 0
        try:
            aggregation = self.collection.where(
                filter=firestore.FieldFilter(
                    "company_key", "==", company_key(company_name)
                )
            ).count()
            # Aggregation results come back as a list of result groups.
            for group in aggregation.get():
                items = group if isinstance(group, list) else [group]
                for item in items:
                    return int(item.value)
            return 0
        except GoogleAPICallError as e:
            logger.warning(f"Failed to get search count: {e}")
            return 0

    def get_cached_query_hashes(self, company_name: str, hashes: list[str]) -> set[str]:
        """Subset of the given query hashes already cached for the company."""
        if self.client is None or not hashes:
            return set()
        key = company_key(company_name)
        found: set[str] = set()
        try:
            for start in range(0, len(hashes), _IN_FILTER_CHUNK):
                chunk = hashes[start : start + _IN_FILTER_CHUNK]
                docs = (
                    self.collection.where(
                        filter=firestore.FieldFilter("company_key", "==", key)
                    )
                    .where(filter=firestore.FieldFilter("query_hash", "in", chunk))
                    .select(["query_hash"])
                    .stream()
                )
                for doc in docs:
                    value = (doc.to_dict() or {}).get("query_hash")
                    if value:
                        found.add(value)
            return found
        except GoogleAPICallError as e:
            logger.warning(f"Failed to check cached queries: {e}")
            return set()


__all__ = [
    "FirestoreSearchCacheRepository",
    "company_key",
    "query_hash",
]
