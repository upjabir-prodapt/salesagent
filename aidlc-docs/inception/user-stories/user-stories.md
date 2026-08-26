# User Stories & Developer Scenarios

## User Story 1: Enterprise Account Executive Research
**As a** Colt Enterprise Sales Account Executive,
**I want to** submit a target company name and receive an evidence-backed Strategic Brief in minutes,
**So that** I can walk into a customer meeting with deep commercial insights and direct Colt product mappings.

### Acceptance Criteria:
- Initiating research via `POST /api/v1/research/initiate` returns `job_id` and `PENDING` status immediately.
- Polling `GET /api/v1/research/status/{job_id}` displays real-time progress percentages and agent milestones.
- Completed job returns comprehensive Markdown report via `GET /api/v1/research/result/{job_id}` and downloadable PDF via `GET /api/v1/research/download/{job_id}`.
- All 12 domains are represented with cited facts and real source URLs.
- Section 8 and 11 map target challenges directly to Colt portfolio solutions.

---

## User Story 2: Cost-Effective Search Caching
**As a** Colt Platform Engineering Lead,
**I want** repeated research runs for the same target company to reuse search results from Redis with a 7-day TTL,
**So that** search API costs and LLM token expenditures are minimized while maintaining high data freshness.

### Acceptance Criteria:
- First run caches all executed search queries and webpage snippets in Redis with `EX 604800`.
- Second run for the same company checks Redis before executing any search, skipping external network calls for cached queries.
- Search count in `cost_attribution` BigQuery reflects only newly executed searches.

---

## Developer Scenario 1: Clean Context Isolation & Workflow Control
**As a** Machine Learning / AI Engineer,
**I want** each sub-agent in the pipeline to receive only its explicitly declared input keys rather than an ever-growing conversation transcript,
**So that** LLM prompt token counts remain predictable, latency stays low, and agents do not suffer from prompt confusion.

### Acceptance Criteria:
- All LLM agents configured with `include_contents="none"`.
- `KeywordGeneratorAgent` writes structured `CandidateQueries`.
- `ParallelSearchAgent` directly manages search execution and populates 12 domain keys in session state.
- `AlignmentAnalyst` and `ReportCompiler` access domain data via templated placeholders (`{{firmographicsagent_output?}}`, etc.).
