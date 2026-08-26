# Code Plan & NFR — Unit 2: KeywordGeneratorAgent

## NFR Assessment
- **Context Isolation**: `include_contents="none"` prevents prior history token accumulation.
- **Reliability**: Structured schema enforcement guarantees valid JSON output matching `CandidateQueries`.

## Code Tasks
- [x] Update `src/worker/agents/sales/query_generator/bm25_selector.py` with 30-query budget and domain limits.
- [x] Update `src/worker/agents/sales/query_generator/factory.py` with `include_contents="none"` and `output_schema=CandidateQueries`.
- [x] Update/add unit tests in `tests/` verifying 30-keyword selection.
