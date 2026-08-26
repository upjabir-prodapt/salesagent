"""Redis repository for search query cache and content storage.

Stores executed web searches in Redis with a 7-day TTL (default 604800s),
keyed by `search:{company_key}:{query_hash}`.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import redis
import redis.asyncio as aioredis

from src.shared.config import settings
from src.shared.logging_config import logger
from src.shared.repositories.clients import get_async_redis_client, get_redis_client


def company_key(company_name: str) -> str:
    """Stable lookup key for a company name (case/whitespace insensitive)."""
    normalized = (company_name or "").strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def query_hash(query: str) -> str:
    """Stable short hash used for query deduplication."""
    return hashlib.sha256((query or "").lower().encode()).hexdigest()[:16]


class RedisSearchCacheRepository:
    """Repository for caching search results and webpage content in Redis."""

    def __init__(
        self,
        client: redis.Redis | None = None,
        async_client: aioredis.Redis | None = None,
    ) -> None:
        self._sync_client = client
        self._async_client = async_client
        self._ttl_seconds = settings.SEARCH_CACHE_TTL_SECONDS

    @property
    def sync_client(self) -> redis.Redis:
        if self._sync_client is None:
            self._sync_client = get_redis_client()
        return self._sync_client

    @property
    def async_client(self) -> aioredis.Redis:
        if self._async_client is None:
            self._async_client = get_async_redis_client()
        return self._async_client

    def _redis_key(self, company_name: str, qhash: str) -> str:
        prefix = getattr(settings, "REDIS_KEY_PREFIX", "salesagent:search:")
        return f"{prefix}{company_key(company_name)}:{qhash}"

    def _company_pattern(self, company_name: str) -> str:
        prefix = getattr(settings, "REDIS_KEY_PREFIX", "salesagent:search:")
        return f"{prefix}{company_key(company_name)}:*"

    def get_search(self, company_name: str, query: str) -> dict[str, Any] | None:
        """Retrieve cached search synchronously."""
        q_hash = query_hash(query)
        key = self._redis_key(company_name, q_hash)
        try:
            raw = self.sync_client.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning(f"[RedisCache] Get failed for key {key}: {exc}")
            return None

    async def async_get_search(
        self, company_name: str, query: str
    ) -> dict[str, Any] | None:
        """Retrieve cached search asynchronously."""
        q_hash = query_hash(query)
        key = self._redis_key(company_name, q_hash)
        try:
            raw = await self.async_client.get(key)
            if not raw:
                return None
            return json.loads(raw)
        except Exception as exc:
            logger.warning(f"[RedisCache] Async get failed for key {key}: {exc}")
            return None

    def set_search(
        self,
        company_name: str,
        query: str,
        results: Any,
        domain: str | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Set cached search synchronously with TTL."""
        q_hash = query_hash(query)
        key = self._redis_key(company_name, q_hash)
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        now = datetime.now(UTC)
        payload = {
            "company_name": company_name,
            "company_key": company_key(company_name),
            "query": query,
            "query_hash": q_hash,
            "domain": domain or "unknown",
            "results": results,
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        }
        try:
            self.sync_client.set(key, json.dumps(payload), ex=ttl)
            return True
        except Exception as exc:
            logger.warning(f"[RedisCache] Set failed for key {key}: {exc}")
            return False

    async def async_set_search(
        self,
        company_name: str,
        query: str,
        results: Any,
        domain: str | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        """Set cached search asynchronously with TTL."""
        q_hash = query_hash(query)
        key = self._redis_key(company_name, q_hash)
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl_seconds
        now = datetime.now(UTC)
        payload = {
            "company_name": company_name,
            "company_key": company_key(company_name),
            "query": query,
            "query_hash": q_hash,
            "domain": domain or "unknown",
            "results": results,
            "cached_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl)).isoformat(),
        }
        try:
            await self.async_client.set(key, json.dumps(payload), ex=ttl)
            return True
        except Exception as exc:
            logger.warning(f"[RedisCache] Async set failed for key {key}: {exc}")
            return False

    def get_searches_for_company(self, company_name: str) -> list[dict[str, Any]]:
        """Retrieve all cached search documents for a company."""
        pattern = self._company_pattern(company_name)
        results: list[dict[str, Any]] = []
        try:
            keys = self.sync_client.keys(pattern)
            if not keys:
                return []
            raw_values = self.sync_client.mget(keys)
            for raw in raw_values:
                if raw:
                    with contextlib.suppress(Exception):
                        results.append(json.loads(raw))
            return results
        except Exception as exc:
            logger.warning(f"[RedisCache] Get all for company failed: {exc}")
            return []

    async def async_get_searches_for_company(
        self, company_name: str
    ) -> list[dict[str, Any]]:
        """Retrieve all cached search documents for a company asynchronously."""
        pattern = self._company_pattern(company_name)
        results: list[dict[str, Any]] = []
        try:
            keys = await self.async_client.keys(pattern)
            if not keys:
                return []
            raw_values = await self.async_client.mget(keys)
            for raw in raw_values:
                if raw:
                    with contextlib.suppress(Exception):
                        results.append(json.loads(raw))
            return results
        except Exception as exc:
            logger.warning(f"[RedisCache] Async get all for company failed: {exc}")
            return []

    def get_cached_query_hashes(self, company_name: str, hashes: list[str]) -> set[str]:
        """Check which query hashes exist in Redis."""
        if not hashes:
            return set()
        keys = [self._redis_key(company_name, qh) for qh in hashes]
        found: set[str] = set()
        try:
            values = self.sync_client.mget(keys)
            for qh, val in zip(hashes, values, strict=False):
                if val:
                    found.add(qh)
            return found
        except Exception as exc:
            logger.warning(f"[RedisCache] Check query hashes failed: {exc}")
            return set()

    async def async_get_cached_query_hashes(
        self, company_name: str, hashes: list[str]
    ) -> set[str]:
        """Check which query hashes exist in Redis asynchronously."""
        if not hashes:
            return set()
        keys = [self._redis_key(company_name, qh) for qh in hashes]
        found: set[str] = set()
        try:
            values = await self.async_client.mget(keys)
            for qh, val in zip(hashes, values, strict=False):
                if val:
                    found.add(qh)
            return found
        except Exception as exc:
            logger.warning(f"[RedisCache] Async check query hashes failed: {exc}")
            return set()

    def count_searches(self, company_name: str) -> int:
        """Count cached searches for a company."""
        pattern = self._company_pattern(company_name)
        try:
            keys = self.sync_client.keys(pattern)
            return len(keys)
        except Exception as exc:
            logger.warning(f"[RedisCache] Count searches failed: {exc}")
            return 0

    async def async_count_searches(self, company_name: str) -> int:
        """Count cached searches for a company asynchronously."""
        pattern = self._company_pattern(company_name)
        try:
            keys = await self.async_client.keys(pattern)
            return len(keys)
        except Exception as exc:
            logger.warning(f"[RedisCache] Async count searches failed: {exc}")
            return 0


__all__ = [
    "RedisSearchCacheRepository",
    "company_key",
    "query_hash",
]
