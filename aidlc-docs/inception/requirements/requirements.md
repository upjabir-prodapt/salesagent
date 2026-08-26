# Requirements Specification — Sales Research Agent

## 1. Domain Coverage (12 Mandatory Enterprise Domains)
The research pipeline must extract, ground, and synthesize facts across 12 distinct enterprise domains:

1. **Firmographics (`firmographicsagent_output`)**: Company name, sector, sub-industry, revenue (current and previous year), employee count, estimated IT spend, market cap, public/private status, stock ticker, founded year, ownership structure, website, business summary.
2. **Geographic Footprint (`geographicagent_output`)**: HQ country and city, all office locations worldwide, data centers, manufacturing/R&D sites, trading regions, regional revenue distribution, countries of operation, expansion plans, supply chain geography.
3. **Executive Leadership (`executiveagent_output`)**: CEO, CFO, COO, CIO, CTO, CISO, and other C-suite leaders (full name, role, start date, previous roles, education, LinkedIn URL, notable achievements, public quotes, leadership style).
4. **Strategy & Challenges (`strategyagent_output`)**: Strategic priorities, transformation goals, digital/cloud/AI plans, M&A strategy, market expansion plans, competitive advantages, sustainability targets, leadership quotes, key business/IT challenges with commercial impact.
5. **Compliance (`complianceagent_output`)**: Applicable regulations and regulatory bodies, data sovereignty requirements, industry certifications (ISO, SOC, PCI-DSS, etc.), audit history, data privacy policies, security frameworks, known compliance issues.
6. **Market Position (`marketagent_output`)**: Revenue breakdown (geography, segment, product line), competitive landscape, market share, market challenges, global trends, emerging focus areas, key customers, procurement model, commercial leverage points, YoY growth, cost drivers, capex plans, supply chain exposure.
7. **Ecosystem & Partnerships (`ecosystemagent_output`)**: Key technology/cloud/connectivity/strategic partners, alliances, dependencies relative to Colt, shared industry bodies, historic Colt engagement, ESG/DEI alignment, co-innovation potential, strategic fit, relationship synergies.
8. **Technology Landscape (`techstackagent_output`)**: Cloud strategy, IT approach, network/cybersecurity approach, known vendors and platforms, infrastructure models, digital/AI/automation investments, digital partnerships, innovation initiatives.
9. **Procurement Patterns (`procurementagent_output`)**: Procurement structure (centralized vs regional), contract/renewal cycles, preferred partners and agreements, budget/IT spend trends, RFP/tender activity, vendor reviews.
10. **Growth Signals (`growthsignals_output`)**: Hiring trends, M&A activity, expansion plans, with detailed signals including type, description, source URL, and sales relevance.
11. **Risk Signals (`risksignals_output`)**: Security incidents, regulatory challenges, compliance issues, with detailed signals including type, description, source URL, and sales relevance.
12. **Campaign Signals (`campaignsignals_output`)**: Active marketing campaigns, advertising spend trends, brand positioning, with detailed signals including type, description, source URL, and sales relevance.

---

## 2. Functional Requirements
- **FR1 (Structured Keyword Generation)**: `KeywordGeneratorAgent` generates 30 targeted search queries across the 12 domains, output as typed Pydantic `CandidateQueries`. BM25 ranking and Jaccard deduplication (>0.7) enforce diversity and eliminate redundancy.
- **FR2 (Context Isolation)**: `include_contents="none"` on all LLM sub-agents ensures that previous conversational history is not forwarded. Only explicitly injected output keys (`{{...output?}}`) are visible to downstream agents.
- **FR3 (Redis 7-Day Web Cache)**: Every search query and its extracted webpage contents are cached in Redis / Cloud Memorystore with a 7-day TTL (`SEARCH_CACHE_TTL_SECONDS=604800`) keyed by `search:{company_key}:{query_hash}`.
- **FR4 (Real Parallel Search)**: Fan out uncached queries concurrently via `google_search_agent` (Gemini Flash with Google Search grounding) bounded by `asyncio.Semaphore(settings.SEARCH_CONCURRENCY_LIMIT)`.
- **FR5 (Deterministic Domain Synthesis)**: Synthesize cached search snippets and URLs into the 12 canonical `DOMAIN_OUTPUT_KEYS` and gate via `validate_domain_outputs_present` (minimum 6 populated domains).
- **FR6 (Colt Catalog Alignment with Context Caching)**: `AlignmentAnalyst` maps target company challenges to Colt solutions using Gemini explicit context caching over the Colt catalog PDF. Output matches Pydantic `ColtAlignmentOutput`.
- **FR7 (Plain LLM Report Compiler)**: `ReportCompiler` compiles the final Strategic Brief markdown report without PlanReAct planner overhead, calling `validate_final_report` once.
- **FR8 (Custom ADK Workflow Agent)**: Orchestrate the entire pipeline using `SalesResearchWorkflowAgent(BaseAgent)` with deterministic `_run_async_impl` control flow.
- **FR9 (Four BigQuery Tables & Cloud Trace)**:
  1. `agent_telemetry`: Per-agent tokens (input/output), latency, cost, and model used.
  2. `cost_attribution`: Total job tokens, search count, search cost, token cost, total USD.
  3. `research_requests`: Job status and lifecycle metadata.
  4. `users_feedback`: User feedback on generated reports.
  5. Cloud Trace: Full trace tree linking API request -> Cloud Task -> Workflow -> Child search spans -> LLM calls.
- **FR10 (Evaluation Rubric & M6 Groundedness)**:
  - Section A (80%): 14-dimension LLM judge rubric (`D1`-`D14`, `M12`, `M13`).
  - Section B (20%): Automated metrics (M1 agent output coverage, M2 completeness, M3 citation groundedness, M4 evidence breadth, M5 semantic similarity via MiniLM ONNX).
  - M6 Metric: Automated domain evidence groundedness verification via `Bm25Verifier`.

---

## 3. Non-Functional Requirements
- **NFR1 (Performance & Latency)**: End-to-end research execution completes within 90-180 seconds for fresh runs, under 60 seconds on warm cache.
- **NFR2 (Cost Optimization)**: Redis 7-day cache eliminates duplicate search costs; context caching cuts catalog token re-ingestion costs by >50%.
- **NFR3 (Reliability & Idempotency)**: Cloud Tasks deduplication via `research-{job_id}`; terminal status skipping; bounded retry with exponential backoff on transient errors.
- **NFR4 (Quality & Test Coverage)**: Maintain test coverage above the 80% CI gate across all modules.

