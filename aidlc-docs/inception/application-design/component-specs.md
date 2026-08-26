# Component Specifications

## 1. `KeywordGeneratorAgent` (LLM Sub-Agent)
- **Base Class**: `RetryingLlmAgent` (ADK `LlmAgent` wrapper)
- **Model**: `Gemini(model=settings.GEMINI_MODEL, retry_options=retry_config)`
- **Configuration**: `include_contents="none"` (enforces context isolation)
- **Output Schema**: Pydantic `CandidateQueries` (structured output: `dict[str, list[str]]`)
- **Ranking**: Post-processed via `Bm25QuerySelector` retuned to 30 queries across 12 domains
- **Output State Key**: `query_generator_output`

## 2. `ParallelSearchAgent` (Deterministic BaseAgent)
- **Base Class**: Google ADK `BaseAgent` (not an `LlmAgent`)
- **Responsibilities**:
  1. Reads `ctx.session.state["query_generator_output"]`.
  2. Queries Redis search cache for each `(company_name, query)` tuple.
  3. Separates cached queries from uncached queries.
  4. Dispatches uncached queries in parallel using `asyncio.gather` bounded by `asyncio.Semaphore(settings.SEARCH_CONCURRENCY_LIMIT)`.
  5. Each search call invokes `google_search_agent` (Gemini Flash with Google Search tool) and extracts URLs, titles, and snippets.
  6. Stores search results in Redis with 7-day TTL (`EX 604800`).
  7. Synthesizes/aggregates search findings into the 12 canonical `DOMAIN_OUTPUT_KEYS`.
  8. Enforces the domain output gate (`validate_domain_outputs_present` with minimum 6 domains).
  9. Creates OpenTelemetry child spans for search milestones.

## 3. `AlignmentAnalyst` (LLM Sub-Agent)
- **Base Class**: `RetryingLlmAgent`
- **Model**: `Gemini(model=settings.GEMINI_MODEL, retry_options=retry_config)`
- **Configuration**: `include_contents="none"`
- **Prompt**: `ALIGNMENT_PROMPT` (injects 12 `DOMAIN_OUTPUT_KEYS`)
- **Context Source**: Gemini explicit context-cached Colt product catalog PDF (`gcs_pdf_loader.py` / `alignment_context.py`)
- **Output Schema**: Pydantic `ColtAlignmentOutput` (contains `alignment_mappings` table + `strategic_opportunity` summary)
- **Output State Key**: `alignment_output`

## 4. `ReportCompiler` (Plain LLM Sub-Agent)
- **Base Class**: `RetryingLlmAgent` (Plain `LlmAgent`, PlanReAct removed)
- **Model**: `Gemini(model=settings.GEMINI_MODEL, retry_options=retry_config)`
- **Configuration**: `include_contents="none"`
- **Tools**: `[validate_final_report_tool]`
- **Prompt**: `REPORT_COMPILER_PROMPT` (fixed section structure, checklist coverage across 12 domains + alignment output)
- **Output State Key**: `final_report`

## 5. `SalesResearchWorkflowAgent` (Custom Root ADK Orchestrator)
- **Base Class**: Google ADK `BaseAgent`
- **Sub-agents**: `[keyword_generator, search_orchestrator, alignment_analyst, report_compiler]`
- **Implementation**: Custom `_run_async_impl(ctx)` async generator yielding events and managing execution state.
