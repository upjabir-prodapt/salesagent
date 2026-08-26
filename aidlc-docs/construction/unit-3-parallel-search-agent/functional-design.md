# Functional Design — Unit 3: ParallelSearchAgent

## 1. Overview
`ParallelSearchAgent` is a custom Google ADK `BaseAgent` that reads the query plan produced by `KeywordGeneratorAgent`, checks Redis for cached search results, executes uncached queries concurrently via Gemini Google Search grounding with bounded concurrency, and caches findings with a 7-day TTL.

## 2. Key Capabilities
- **Redis Query Lookup**: Checks `search:{company_key}:{query_hash}` before calling external search.
- **Bounded Concurrency**: Uses `asyncio.Semaphore(settings.SEARCH_CONCURRENCY_LIMIT)` (default 8) to avoid quota starvation.
- **Search Execution**: Invokes `google_search_agent` (Gemini 2.5 Flash + Google Search grounding) for uncached queries.
- **Grounding Extraction**: Parses `grounding_metadata` (web URLs, snippet texts, and source titles).
- **TTL Persistence**: Writes results to Redis with `EX 604800`.
- **Search Telemetry**: Updates `state["search_count"]` and accumulates search evidence for downstream evaluation.
