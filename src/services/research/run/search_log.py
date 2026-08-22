"""Search query accounting for cost attribution and the search cache.

Every live web search issued by the agents is recorded into session state here.
The finalization pipeline flushes the accumulated rows to the Firestore
``search_cache`` collection and derives ``search_count`` for cost attribution.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from ....core.logging_config import logger

SEARCH_QUERY_RECORDS_KEY = "search_query_records"
SEARCH_COUNT_KEY = "mc_search_count"

__all__ = [
    "SEARCH_QUERY_RECORDS_KEY",
    "SEARCH_COUNT_KEY",
    "query_hash",
    "record_search_query",
    "get_search_count",
    "get_search_query_records",
]


def query_hash(query: str) -> str:
    """Stable short hash used for query deduplication in the search cache."""
    return hashlib.sha256(query.lower().encode()).hexdigest()[:16]


def record_search_query(
    state: Any,
    *,
    query: str,
    agent_name: str,
    entries: list[dict] | None = None,
    domain: str | None = None,
) -> None:
    """Append one executed search to session state and bump the search counter."""
    if state is None or not hasattr(state, "__setitem__"):
        return
    query = (query or "").strip()
    if not query:
        return

    records: list[dict] = list(state.get(SEARCH_QUERY_RECORDS_KEY) or [])
    records.append(
        {
            "query": query,
            "query_hash": query_hash(query),
            "domain": domain or agent_name,
            "agent": agent_name,
            "entries": entries or [],
            "searched_at": datetime.now(UTC).isoformat(),
        }
    )
    state[SEARCH_QUERY_RECORDS_KEY] = records
    state[SEARCH_COUNT_KEY] = len(records)

    logger.debug(
        f"[SearchLog] recorded search #{len(records)} agent={agent_name} "
        f"query={query[:80]!r} results={len(entries or [])}"
    )


def get_search_query_records(state: Any) -> list[dict]:
    if state is None:
        return []
    return list(state.get(SEARCH_QUERY_RECORDS_KEY) or [])


def get_search_count(state: Any) -> int:
    """Number of searches issued during the run (0 when nothing was recorded)."""
    if state is None:
        return 0
    count = state.get(SEARCH_COUNT_KEY)
    if isinstance(count, int) and count >= 0:
        return count
    return len(get_search_query_records(state))
