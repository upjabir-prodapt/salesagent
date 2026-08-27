# Sales Intelligence Research Agent

An enterprise AI-powered research platform built for Colt Technology Services. The platform automates deep, multi-source company research across 12 business domains, maps target challenges to the Colt solution catalog, and compiles executive-ready Strategic Brief reports with downloadable PDF artifacts.

---

## Architecture Overview

The system follows a decoupled API + Cloud Tasks Worker architecture built on Google ADK (Agent Development Kit), Gemini 2.5 Pro / Flash, and Redis:

```
Browser / AI-Hub-UI (Client)
         │ HTTP (POST /api/v1/research/initiate)
         ▼
Sales Agent API (src/api/)
  - IAP Auth & Firestore Entitlements
  - BigQuery record creation (PENDING)
  - Enqueue Google Cloud Task with OIDC auth
         │ Google Cloud Tasks (OIDC HTTP POST)
         ▼
Sales Agent Worker (src/worker/)
  - OIDC token verification
  - Executes ResearchPipeline: 4 independent Agent steps, each owning its
    own RetryPolicy (no shared root agent, no shared session state)
         │
         ├─ 1. QueryPlanner (AdkAgentStep)
         │     30 keywords across 12 domains (CandidateQueries, BM25-ranked)
         │     -> typed QueryPlan
         │
         ├─ 2. SearchExecutor (Agent, bypasses ADK)
         │     Redis 7-day cache check + async token-bucket RateLimiter
         │     (real QPS control) + per-query retry with honest failures
         │     -> typed SearchFindings (12 canonical domains)
         │
         ├─ 3. AlignmentAnalyst (AdkAgentStep)
         │     Colt catalog injected directly into the prompt (no tool call)
         │     -> typed ColtAlignment
         │
         └─ 4. ReportCompiler (AdkAgentStep)
               Input: CompilerInput(findings, alignment) only
               In-process OutputGuardrail validation triggers step retry
         │
         ▼
Finalization & Telemetry
  - WeasyPrint PDF compilation & GCS upload
  - EvaluationService (Section A 80% + Section B 20% + M6 domain groundedness)
  - BigQuery telemetry streaming across 4 tables
  - OpenTelemetry distributed tracing (Cloud Trace)
```

---

## 12 Research Domains

1. **Firmographics**: Revenue, employee count, market cap, ownership, IT spend.
2. **Geographic Footprint**: HQ, regional offices, data centers, trading regions.
3. **Executive Leadership**: C-suite profiles, roles, quotes, achievements.
4. **Strategy & Challenges**: Transformation goals, digital/AI plans, pain points.
5. **Compliance**: Regulations, sovereignty, certifications, audit history.
6. **Market Position**: Market share, competitors, customers, growth drivers.
7. **Ecosystem & Partnerships**: Strategic alliances, technology partners.
8. **Technology Landscape**: Cloud, cybersecurity, infrastructure, digital roadmap.
9. **Procurement Patterns**: Centralized vs regional, contract cycles, RFPs.
10. **Growth Signals**: Hiring trends, M&A activity, expansions.
11. **Risk Signals**: Security incidents, regulatory challenges.
12. **Campaign Signals**: Active advertising campaigns, brand initiatives.

---

## Quickstart & Local Development

### 1. Installation
```bash
# Install dependencies using uv
uv sync
```

### 2. Configuration
```bash
cp .env.example .env
# Edit .env with your GCP project and Redis settings
```

### 3. Running Locally
```bash
# Run API service (port 8080)
DOTENV_PATH=.env uv run python main_api.py

# Run Worker service (port 8081)
PORT=8081 WORKER_SKIP_OIDC_VERIFICATION=true DOTENV_PATH=.env uv run python main_worker.py
```

### 4. Running Tests & Quality Checks
```bash
# Run full test suite with coverage gate (>=80%)
uv run pytest tests/ --cov=src --cov-fail-under=80

# Lint and format
uv run ruff check .
uv run ruff format . --check
```

---

## AI-DLC Documentation

Complete architecture decisions, component specifications, and functional design documents are maintained in `aidlc-docs/`:
- `aidlc-docs/aidlc-state.md` — Master stage and unit progress tracker.
- `aidlc-docs/audit.md` — Audit trail of architecture decisions.
- `aidlc-docs/inception/` — Requirements, architecture overview, component specs, data models, and API contracts.
- `aidlc-docs/construction/` — Functional designs, NFR assessments, code plans, code summaries, and verification checklists.
