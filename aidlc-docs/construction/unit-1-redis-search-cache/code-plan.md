# Code Generation Plan — Unit 1: Redis Search Cache

## Files to Create / Modify:
- [x] `pyproject.toml` — Add `redis>=5.0.0` dependency.
- [x] `src/shared/config.py` — Add Redis host, port, db, password, tls, and TTL settings.
- [x] `.env.example` — Document Redis environment variables.
- [x] `src/shared/repositories/clients.py` — Add `get_redis_client()` and `get_async_redis_client()`.
- [x] `src/shared/repositories/redis_repository.py` — Implement `RedisSearchCacheRepository`.
- [x] `src/shared/repositories/__init__.py` — Export `RedisSearchCacheRepository`.
- [x] `src/worker/search_cache/service.py` — Adapt `SearchCacheService` to default to Redis when enabled.
- [x] `tests/shared/test_redis_repository.py` — Unit tests for sync and async cache operations.
