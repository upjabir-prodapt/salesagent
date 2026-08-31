# Sales Intelligence Research Agent

An enterprise AI-powered research platform engineered for **Colt Technology Services**. The platform automates multi-source account research across **12 business domains**, maps target enterprise challenges directly to the **Colt solution catalog**, and compiles executive-ready **Strategic Account Briefs** with downloadable PDF artifacts, real citation grounding, and automated evaluation.

---

## Table of Contents
- [Architecture Overview](#architecture-overview)
- [The 4-Step Research Pipeline](#the-4-step-research-pipeline)
  - [1. QueryPlanner](#1-queryplanner)
  - [2. SearchExecutor](#2-searchexecutor)
  - [3. AlignmentAnalyst](#3-alignmentanalyst)
  - [4. ReportCompiler](#4-reportcompiler)
- [Data Flow & Typed Contracts](#data-flow--typed-contracts)
- [12 Research Domains & 13 Report Sections](#12-research-domains--13-report-sections)
- [Search Grounding, Citations & Anti-Hallucination](#search-grounding-citations--anti-hallucination)
- [Infrastructure, Storage & Telemetry](#infrastructure-storage--telemetry)
  - [Redis Search Cache](#redis-search-cache)
  - [BigQuery Telemetry & Cost Accounting](#bigquery-telemetry--cost-accounting)
  - [Google Cloud Storage & WeasyPrint PDF Generation](#google-cloud-storage--weasyprint-pdf-generation)
  - [OpenTelemetry & Cloud Trace](#opentelemetry--cloud-trace)
  - [Dual-Region Architecture](#dual-region-architecture)
- [Evaluation & Quality Scoring](#evaluation--quality-scoring)
- [Developer Guide & Local Development](#developer-guide--local-development)
  - [Prerequisites & Installation](#prerequisites--installation)
  - [Configuration (.env)](#configuration-env)
  - [Running Locally](#running-locally)
  - [Running Unit & Integration Tests](#running-unit--integration-tests)
- [Repository Structure](#repository-structure)

---

## Architecture Overview

The system implements a decoupled **API + Worker** architecture deployed on Google Cloud Run, communicating asynchronously via Google Cloud Tasks:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Browser / AI-Hub UI                                │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ HTTP (POST /api/v1/research/initiate)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       Sales Agent API (src/api/)                            │
│  - Identity-Aware Proxy (IAP) Auth & Group Entitlements                     │
│  - Session token management (colt_session JWT)                              │
│  - BigQuery record creation (Initial PENDING status)                        │
│  - Enqueues Cloud Task with W3C distributed traceparent                     │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ Google Cloud Tasks (OIDC HTTP POST)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Sales Agent Worker (src/worker/)                       │
│  - OIDC token authentication (require_cloud_tasks_oidc)                     │
│  - Executes ResearchPipeline: 4 independent, typed Agent steps              │
│                                                                             │
│  1. QueryPlanner (AdkAgentStep)                                             │
│     Generates domain queries -> BM25 selector -> 30 targeted queries        │
│     Output: QueryPlan                                                       │
│                                                                             │
│  2. SearchExecutor (Agent, custom QPS & cache engine)                       │
│     Redis 7-day cache check + async token-bucket RateLimiter + Semaphore    │
│     Executes Gemini with Google Search Grounding -> captures Evidence       │
│     Output: SearchFindings (12 canonical domains)                           │
│                                                                             │
│  3. AlignmentAnalyst (AdkAgentStep)                                         │
│     Maps target challenges to Colt Product Catalog (DCA, SD-WAN, NaaS)      │
│     Output: ColtAlignment (mappings + strategic opportunity + opening hooks)│
│                                                                             │
│  4. ReportCompiler (AdkAgentStep)                                           │
│     Compiles 13-section Markdown report + 300+ verified citation URLs       │
│     Dual validation gates: OutputGuardrail + Bm25Verifier groundedness      │
│     Targeted retry-as-revision feedback loop on validation failure          │
│     Output: Report                                                          │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        Finalization & Post-Processing                       │
│  - WeasyPrint PDF Generation & GCS Upload with signed download URL          │
│  - EvaluationService (Section A LLM Judge 80% + Section B Metrics 20%)      │
│  - BigQuery Telemetry Streaming (cost_attribution, agent_telemetry)         │
│  - Status update: COMPLETED (100% progress)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
```
---

## The 4-Step Research Pipeline

Rather than a monolithic agent sharing mutable session state, the system executes a modular, 4-step pipeline where each step is an independent `Agent` instance owning its own `RetryPolicy` and typed inputs/outputs:

```
QueryPlanner ──[QueryPlan]──▶ SearchExecutor ──[SearchFindings]──▶ AlignmentAnalyst ──[ColtAlignment]──▶ ReportCompiler ──[Report]
```

### 1. QueryPlanner
* **Implementation**: `AdkAgentStep[ResearchRequest, QueryPlan]` in `src/worker/agents/planner.py`.
* **Model**: Gemini 3.5 Flash via `RegionalGemini`.
* **Process**:
  1. Receives `ResearchRequest(company="TargetCompany")`.
  2. Generates 3–5 targeted search queries for each of the 12 canonical research domains using `CandidateQueries` structured JSON output.
  3. Uses `Bm25QuerySelector` to score and deduplicate queries against a curated domain corpus, selecting the **top 30 most informative queries**.
* **Output**: Immutable `QueryPlan(company, queries)`.

### 2. SearchExecutor
* **Implementation**: Custom `Agent[QueryPlan, SearchFindings]` in `src/worker/agents/search.py`.
* **Architecture**: Bypasses ADK session overhead to execute high-throughput web searches concurrently.
* **Process**:
  1. **Cache Partitioning**: Queries Redis (`salesagent:search:{company}:{query_hash}`) for existing cached results (7-day TTL).
  2. **Rate Limiting & Concurrency**: Manages uncached queries using an async token-bucket `RateLimiter` (configured QPS and burst capacity) plus an `asyncio.Semaphore` (bounded concurrency). Automatically halves rate on HTTP 429 (`penalize()`).
  3. **Search Execution**: Invokes Gemini with native `google_search` grounding.
  4. **Grounding & Evidence Extraction**: Parses `grounding_metadata` and `grounding_chunks` from Google Search responses into structured `Evidence` objects (URL, title, snippet, query, authoritative domain flag).
  5. **Fault Tolerance**: Retries transient failures with exponential backoff & jitter. Records query results honestly (`QueryResult.ok()` vs `QueryResult.failed()`) without fabricating text.
  6. **Assembly**: Assembles results into 12 domain findings. Validates minimum success rate (`SEARCH_MIN_SUCCESS_RATE=0.6`).
* **Output**: `SearchFindings(company, domains, executed, failed)`.

### 3. AlignmentAnalyst
* **Implementation**: `AdkAgentStep[SearchFindings, ColtAlignment]` in `src/worker/agents/alignment.py`.
* **Model**: Gemini 3.5 Flash with `ColtAlignmentOutputSchema`.
* **Process**:
  1. Reads all 12 domain outputs from `SearchFindings`.
  2. Reads the mounted Colt Product Catalog (pre-warmed during startup from `ColtProductCatalog.pdf`).
  3. Maps target account pain points, technology transitions (e.g. SAP S/4HANA migration, hybrid-cloud expansion), and compliance requirements directly to Colt solutions (Dedicated Cloud Access, SD-WAN/SASE, On Demand NaaS, Dark Fibre, MANs, Sovereign Cloud connectivity).
  4. Generates commercial value propositions, executive opening hooks, and urgency drivers.
* **Output**: `ColtAlignment(mappings, strategic_opportunity, hooks)`.

### 4. ReportCompiler
* **Implementation**: `AdkAgentStep[CompilerInput, Report]` in `src/worker/agents/compiler.py`.
* **Model**: Gemini 3.5 Flash generating comprehensive Markdown.
* **Process**:
  1. **Input**: Takes `CompilerInput(findings, alignment)` and renders a complete prompt with domain findings, alignment mappings, and a de-duplicated **Verified Source URLs** block (300+ citation links from search grounding).
  2. **Generation**: Compiles the final **13-Section Strategic Account Brief** adhering to strict structural and formatting rules.
  3. **Dual Validation Gates**:
     * **Gate 1 (`OutputGuardrail`)**: Verifies that all 13 sections and required markdown tables (Location Breakdown, Colt Alignment Table) exist.
     * **Gate 2 (`Bm25Verifier`)**: BM25-scores every factual sentence against the `SearchFindings.all_evidence()` grounding corpus to detect and block hallucinated claims.
  4. **Retry-as-Revision**: If validation fails, the compiler does **not** regenerate blind from scratch. Instead, the next retry attempt is fed its own prior draft plus the specific validation failure feedback to produce a targeted revision.
* **Output**: `Report(markdown, validation_status, validation_violations)`.


---

## Data Flow & Typed Contracts

All communication between pipeline steps uses immutable, typed dataclasses defined in `src/worker/agents/models.py`:

```
┌────────────────────────────────────────────────────────┐
│ ResearchRequest(job_id: str, company: str)             │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ QueryPlan                                              │
│  - company: str                                        │
│  - queries: tuple[Query(text, domain), ...] (30 items) │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ SearchFindings                                         │
│  - company: str                                        │
│  - domains: Mapping[str, DomainFinding] (12 domains)   │
│      - DomainFinding(domain, content, evidence)        │
│          - Evidence(url, title, snippet, query, ...)   │
│  - executed: int, failed: tuple[str, ...]              │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ ColtAlignment                                          │
│  - mappings: tuple[ColtAlignmentMapping, ...]          │
│  - opportunity_summary: str                            │
│  - hooks: tuple[str, ...]                              │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ CompilerInput(findings, alignment)                     │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ Report(markdown: str, validation_status: str)          │
└──────────────────────────┬─────────────────────────────┘
                           ▼
┌────────────────────────────────────────────────────────┐
│ PipelineResult(report, findings, alignment, telemetry) │
│  - to_legacy_state() -> dict (bridge to finalization)  │
└────────────────────────────────────────────────────────┘
```

---

## 12 Research Domains & 13 Report Sections

### 12 Research Domains (Input Investigation)
1. **`firmographics`**: Revenue, profit margins, employee count, global corporate hierarchy, subsidiaries.
2. **`geographic`**: Physical headquarters, regional offices, manufacturing plants, data centers, regional hubs.
3. **`executive`**: C-suite leadership (CEO, CIO, CTO, CFO, CISO), executive interviews, strategic quotes.
4. **`strategy`**: Multi-year transformation roadmaps, cloud-first initiatives, smart manufacturing ("Factory of the Future").
5. **`compliance`**: Regulatory frameworks (EU CSDDD/CS3D, CSRD, GDPR, EUDR), audits, data privacy posture.
6. **`market`**: Industry rank, market share, competitive positioning vs peer group, market dynamics.
7. **`ecosystem`**: Technology partnerships (SAP, Microsoft, AWS, Google Cloud, Cisco), systems integrators.
8. **`tech_stack`**: Enterprise WAN, SD-WAN, SASE, cloud service providers, core ERP platforms (SAP S/4HANA).
9. **`procurement`**: Sourcing models (centralized vs regional hubs), RFP patterns, vendor consolidation criteria.
10. **`growth_signals`**: M&A transactions, joint ventures, capital investments, regional facility expansions.
11. **`risk_signals`**: Cyber security incidents, supply chain bottlenecks, regulatory investigations, outages.
12. **`campaign_signals`**: Active brand campaigns, marketing initiatives, sustainability disclosures.

### 13 Report Sections (Compiled Strategic Account Brief)
- **Company Snapshot**: Key financial and operational metrics summary table.
- **1. Company Overview**: Business model, global presence, operational location breakdown table.
- **2. Key Executive Bios**: Structured profiles for key C-suite leaders with quotes and initiatives.
- **3. Strategic Priorities and Business Goals**: 2–5 year transformation objectives.
- **4. Current Market Position & Outlook**: Financial performance, segment analysis, competitor benchmarking.
- **5. Technology Landscape**: Infrastructure, cloud architecture, network topology, and IT spend.
- **6. Key Business & IT Challenges**: Operational pain points and digital friction areas.
- **7. Procurement & Technology Buying Patterns**: Sourcing governance and evaluation criteria.
- **8. Colt Technology Alignment Table**: Structured challenge-to-Colt-solution mapping table.
- **9. Relationship Landscape & Potential Synergies**: Partner ecosystem synergies.
- **10. Regional Spend & Infrastructure Overlay**: Regional IT spend distribution and network overlay.
- **11. Strategic Opportunity & Live Call Readiness**: Sales play, elevator pitch, executive opening hooks.
- **12. Signals**: Detailed prose covering growth, risk, and campaign triggers.
- **13. Source Summary**: Comprehensive, de-duplicated list of verified search grounding citation URLs.
---

## Search Grounding, Citations & Anti-Hallucination

```
30 Search Queries
       │
       ▼
Google Vertex AI Search Grounding (Crawls & reads live web pages)
       │
       ├──────────────────────────────────────────┬──────────────────────────────────────────┐
       ▼                                          ▼                                          ▼
[Factual Content Synthesized]             [Citation URLs & Metadata]                 [Evidence Objects]
       │                                          │                                          │
       ▼                                          ▼                                          ▼
Injected into Prompt                      Injected into Prompt                       Passed to Bm25Verifier
(Builds Sections 1–12 of the Report)      (Builds Section 13 Source Summary)         (Validates Report Grounding)
```

1. **Native Google Search Grounding**: `SearchExecutor` invokes Gemini models with `GoogleSearch()` grounding enabled. Google's search engine retrieves and extracts factual text from live web sources.
2. **Real Citation Provenance**: Grounding chunk metadata (`web.uri`, `web.title`, and text spans) is preserved as structured `Evidence` items.
3. **Verified Source URLs Injection**: `ReportCompiler` receives all de-duplicated grounding URLs directly in the prompt and reproduces them in **Section 13 (Source Summary)**, ensuring complete citation transparency (typically 300+ unique source URLs per report).
4. **BM25 Groundedness Verification Gate**: In addition to format checks, `ReportCompiler` uses `Bm25Verifier` to compare every factual claim in the drafted report against `findings.all_evidence()`. If claims lack grounding support in search results, the draft is rejected and retried with targeted revision feedback.

---

## Infrastructure, Storage & Telemetry

### Redis Search Cache
- **Backend**: Google Cloud Memorystore for Redis.
- **Namespace Isolation**: `REDIS_KEY_PREFIX=salesagent:search:` ensures key isolation on shared Memorystore clusters (e.g. sharing with Translation service).
- **TTL**: 7-day expiration (`604800` seconds).
- **Format**: `salesagent:search:{company_slug}:{query_hash}` storing query response text and citation sources.

### BigQuery Telemetry & Cost Accounting
The system streams execution data across four BigQuery tables:
- **`research_requests`**: Job execution state, progress percentage (5% -> 25% -> 50% -> 75% -> 92% -> 97% -> 100%), current agent step, final markdown report, and error details.
- **`cost_attribution`**: Reconciles exact model token usage and Google Search grounding call counts against the pricing catalog (`pricing_catalog.json`) to compute exact USD cost per job execution.
- **`agent_telemetry`**: Per-agent telemetry records capturing execution latency, model name, input/output tokens, and error classification.
- **`users_feedback`**: Captures user ratings (1–5) and qualitative feedback submitted for compiled briefs.

### Google Cloud Storage & WeasyPrint PDF Generation
- Upon report compilation, `ResearchArtifactService` converts the Markdown brief to styled HTML and renders an executive PDF using **WeasyPrint** (with embedded SVG charts, executive styling, and page numbering).
- Uploads the PDF to GCS (`gs://{bucket}/salesagent_response/{job_id}/final_report.pdf`).
- Generates a time-bounded signed URL for secure client download.

### OpenTelemetry & Cloud Trace
- Distributed tracing instrumented via OpenTelemetry (`src/shared/otel_setup.py`).
- Trace context (`traceparent` / `tracestate`) is propagated across HTTP boundaries from API to Cloud Tasks to Worker.
- Spans cover pipeline execution, individual agent steps, and Google GenAI SDK model invocations.
- Exports traces directly to **Google Cloud Trace**.

### Dual-Region Architecture
- **Infrastructure Region (`GOOGLE_CLOUD_LOCATION`)**: `europe-west1` (Cloud Tasks queues, GCS buckets, BigQuery datasets, Redis Memorystore).
- **Inference Region (`VERTEX_AI_LOCATION`)**: `europe-west3` (Gemini 3.5 Flash and Gemini 2.5 Pro Vertex AI endpoints).
- `src/worker/model.py::RegionalGemini` decouples the Vertex AI inference region from the infrastructure location, ensuring optimal model availability and pricing.

---

## Evaluation & Quality Scoring

Every completed research report undergoes automated quality assessment via `EvaluationService` (`src/worker/evaluation/`):

$$\text{Overall Score} = (0.80 \times \text{Section A Score}) + (0.20 \times \text{Section B Score})$$

### Section A: LLM Judge (80% Weight)
An independent LLM Judge evaluates the report across 5 qualitative criteria (1–5 scale each):
1. **Relevance**: Alignment with target company facts and Colt sales context.
2. **Completeness**: Thoroughness of company facts across all 12 domains.
3. **Actionability**: Value of opening hooks and commercial recommendations for sales reps.
4. **Structure & Clarity**: Professional executive brief formatting, readability, and tables.
5. **Colt Solution Alignment**: Strategic fit and realism of proposed Colt products.

### Section B: Deterministic Metrics (20% Weight)
1. **M1 — Agent Output Coverage**: Ratio of populated domain outputs (target: 12/12).
2. **M2 — Report Completeness**: Presence and population of all 13 required sections.
3. **M3 — Citation Groundedness**: Verification of Section 13 cited domains against captured search evidence.
4. **M4 — Evidence Breadth**: Domain diversity of citations (target: >= 10 unique domains).
5. **M5 — Semantic Groundedness**: Vector similarity between executive summary claims and verified facts.
6. **M6 — Domain Groundedness**: BM25 claim support across individual domain findings.

---

## Developer Guide & Local Development

### Prerequisites & Installation
- Python 3.11+
- [`uv`](https://github.com/astral-sh/uv) (fast Python package manager)
- Google Cloud SDK (`gcloud`) authenticated with access to a GCP project with Vertex AI enabled.

```bash
# Clone the repository
git clone https://github.com/your-org/Sales-Agent.git
cd Sales-Agent

# Install all dependencies into virtual environment
uv sync
```

### Configuration (.env)
Copy the example configuration files:
```bash
cp .env.example .env.worker.local
cp .env.example .env.api.local
```

Key environment variables:
```ini
# Google Cloud
GOOGLE_CLOUD_PROJECT=your-gcp-project
GOOGLE_CLOUD_LOCATION=europe-west1
VERTEX_AI_LOCATION=europe-west3

# Models
LLM_MODEL=gemini-3.5-flash
SEARCH_AGENT_MODEL=gemini-3.5-flash
EVALUATOR_MODEL=gemini-3.5-flash

# Search & Execution
SEARCH_QPS=4.0
SEARCH_QPS_BURST=8
SEARCH_CONCURRENCY_LIMIT=8
SEARCH_TIMEOUT_SECONDS=60.0
COMPILER_TIMEOUT_SECONDS=300.0

# Redis Cache
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
REDIS_KEY_PREFIX=salesagent:search:
```

### Running Locally

```bash
# Terminal 1: Run Worker Service (Port 8086)
PORT=8086 WORKER_SKIP_OIDC_VERIFICATION=true DOTENV_PATH=.env.worker.local uv run python main_worker.py

# Terminal 2: Run Public API Service (Port 8085)
PORT=8085 DOTENV_PATH=.env.api.local uv run python main_api.py
```

### Running Unit & Integration Tests

```bash
# Run full test suite
uv run pytest

# Run test suite with coverage report (gate >= 80%)
uv run pytest --cov=src --cov-fail-under=80

# Run specific agent unit tests
uv run pytest tests/worker/agents/

# Linting and formatting checks
uv run ruff check src/ tests/
uv run ruff format src/ tests/ --check
```

---

## Repository Structure

```
Sales-Agent/
├── main_api.py                       # FastAPI entrypoint for Public API service
├── main_worker.py                    # FastAPI entrypoint for Cloud Tasks Worker service
├── src/
│   ├── api/                          # Public REST API
│   │   ├── core/                     # IAP authentication, JWT tokens, security
│   │   ├── routes/                   # Endpoint routers (/initiate, /status, /result, /download)
│   │   ├── schemas/                  # Request/response Pydantic models
│   │   └── services/                 # CloudTasks enqueue service, Job status service
│   ├── worker/                       # Research Swarm & Execution Engine
│   │   ├── agents/                   # The 4 Pipeline Step Agents
│   │   │   ├── base.py               # Agent base class, RetryPolicy, ErrorKind, AdkAgentStep
│   │   │   ├── models.py             # Typed contracts (QueryPlan, SearchFindings, Report)
│   │   │   ├── planner.py            # Step 1: QueryPlanner (BM25 query selection)
│   │   │   ├── search.py             # Step 2: SearchExecutor (RateLimiter, Google Search)
│   │   │   ├── alignment.py          # Step 3: AlignmentAnalyst (Colt catalog mappings)
│   │   │   ├── compiler.py           # Step 4: ReportCompiler (Markdown brief, BM25 gate)
│   │   │   ├── safety.py             # Safety thresholds & policies
│   │   │   └── tools/                # Evidence store, BM25 verifier, GCS PDF catalog loader
│   │   ├── api/                      # Worker HTTP routes (/internal/tasks/research)
│   │   ├── evaluation/               # Section A (LLM Judge) & Section B (Metrics) evaluation
│   │   ├── runtime/                  # Pricing calculator, cost reconciliation
│   │   ├── services/                 # ResearchJobRunner, WeasyPrint PDF generator, Finalization
│   │   ├── model.py                  # RegionalGemini (decoupled Vertex AI inference region)
│   │   ├── observers.py              # TelemetryObserver, ProgressObserver, TracingObserver
│   │   └── pipeline.py               # ResearchPipeline (orchestrates the 4 steps)
│   └── shared/                       # Shared infrastructure & utilities
│       ├── config.py                 # Central Pydantic Settings
│       ├── exceptions.py             # App exception hierarchy
│       ├── logging_config.py         # Structured JSON logging
│       ├── otel_setup.py             # OpenTelemetry TracerProvider & Cloud Trace exporter
│       ├── repositories/             # BigQuery, GCS, Redis, and Firestore client repos
│       ├── schemas/                  # Cross-service task schemas (ResearchTaskPayload)
│       └── utils/                    # Guardrails (PII/Prompt injection), URL utilities
├── tests/                            # Comprehensive test suite (414+ tests)
├── pyproject.toml                    # Project dependencies and configuration
└── README.md                         # This file
```

> **Note:** Mounted runtime assets (`assets/`, `data/`, `ColtProductCatalog.pdf`) and internal
> planning/review artifacts (`improvements.md`, `IMPLEMENTATION_PLAN.md`, `aidlc-docs/`) are
> intentionally excluded from this repository (see `.gitignore`). Production and CI resolve the
> pricing catalog and Colt product catalog exclusively from the GCS-mounted volume configured in
> `.gitlab-ci.yml` (`--add-volume ... type=cloud-storage`); for local development, place your own
> copies under `assets/` or set `ASSETS_ROOT` to a local path (see `src/shared/config.py`).
