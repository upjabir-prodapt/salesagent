"""Tests for RedisSearchCacheRepository."""

from unittest.mock import MagicMock

import pytest

from src.shared.repositories.redis_repository import (
    RedisSearchCacheRepository,
    company_key,
    query_hash,
)


def test_company_key_and_query_hash():
    c_key = company_key("Acme Corp")
    assert isinstance(c_key, str)
    assert len(c_key) == 16
    assert c_key == company_key("  acme corp  ")

    q_hash = query_hash("Acme revenue 2025")
    assert isinstance(q_hash, str)
    assert len(q_hash) == 16
    assert q_hash == query_hash("acme revenue 2025")


def test_redis_search_cache_sync_operations():
    mock_redis = MagicMock()
    repo = RedisSearchCacheRepository(client=mock_redis)

    # Test set_search
    success = repo.set_search(
        company_name="Acme Corp",
        query="acme revenue 2025",
        results=[
            {"url": "https://example.com", "title": "Acme", "content": "Revenue is 10M"}
        ],
        domain="firmographics",
        ttl_seconds=600,
    )
    assert success is True
    assert mock_redis.set.called

    # Test get_search hit
    mock_redis.get.return_value = '{"company_name": "Acme Corp", "query": "acme revenue 2025", "results": [{"content": "10M"}]}'
    res = repo.get_search("Acme Corp", "acme revenue 2025")
    assert res is not None
    assert res["company_name"] == "Acme Corp"

    # Test get_search miss
    mock_redis.get.return_value = None
    assert repo.get_search("Acme Corp", "unknown query") is None


@pytest.mark.asyncio
async def test_redis_search_cache_async_operations():
    mock_async_redis = MagicMock()
    repo = RedisSearchCacheRepository(async_client=mock_async_redis)

    # Async mock methods
    async def async_set(*args, **kwargs):
        return True

    async def async_get(*args, **kwargs):
        return '{"company_name": "Acme Corp", "query": "acme ceo", "results": [{"content": "Jane Doe"}]}'

    mock_async_redis.set = async_set
    mock_async_redis.get = async_get

    success = await repo.async_set_search(
        company_name="Acme Corp",
        query="acme ceo",
        results=[{"content": "Jane Doe"}],
        domain="executive",
    )
    assert success is True

    cached = await repo.async_get_search("Acme Corp", "acme ceo")
    assert cached is not None
    assert cached["query"] == "acme ceo"


def test_redis_search_cache_key_prefix():
    repo = RedisSearchCacheRepository()
    c_key = company_key("Acme Corp")
    q_h = query_hash("acme ceo")
    key = repo._redis_key("Acme Corp", q_h)
    pattern = repo._company_pattern("Acme Corp")
    assert key.startswith("salesagent:search:")
    assert key == f"salesagent:search:{c_key}:{q_h}"
    assert pattern == f"salesagent:search:{c_key}:*"
