# NFR Assessment — Unit 1: Redis Search Cache

## 1. Latency & Performance
- **Target**: Cache lookup latency < 5ms (vs 1500-3000ms for external Google Search API call).
- **Optimization**: Pipelined / batched `mget` for multi-query hash lookups (`get_cached_query_hashes`).

## 2. Reliability & Fault Tolerance
- **Graceful Fallback**: If Redis connection fails or times out, the repository logs a warning and returns `None` (cache miss), allowing searches to continue without failing the research job.
- **Connect Timeout**: Capped at `settings.REDIS_CONNECT_TIMEOUT_SECONDS = 2.0` to avoid blocking research worker threads.

## 3. Security
- Supports Redis TLS (`REDIS_TLS_ENABLED=true`) and AUTH password (`REDIS_PASSWORD`) for Cloud Memorystore with transit encryption.
