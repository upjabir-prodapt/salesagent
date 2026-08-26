# Code Summary — Unit 2: KeywordGeneratorAgent

## Files Created / Modified
- `src/worker/agents/sales/query_generator/bm25_selector.py` — Updated `TOTAL_BUDGET = 30` and domain limits to distribute 30 queries across all 12 domains.
- `src/worker/agents/sales/query_generator/factory.py` — Configured `output_schema=CandidateQueries` and `include_contents="none"`.
- `src/worker/agents/sales/composition/leaf.py` — Updated `create_llm_agent` with `output_schema` and `include_contents="none"` support.

## Test Results
- Unit tests verify 30-query budget and deduplication.
