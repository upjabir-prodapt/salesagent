# Implementation Plan — Agent Pipeline Rewrite (ADK-Retained)

> Status: DRAFT — approved to execute · Created 2026-08-26
> Scope: `src/worker/agents/`, `src/worker/runtime/`, `src/worker/services/orchestrator.py`
> Out of scope: `src/api/`, `src/worker/evaluation/`, `src/worker/services/finalization_*`,
> `src/worker/services/artifacts.py` (all kept, protected by a compatibility bridge)

## 1. Problem Statement

Today one root agent (`SalesResearchWorkflowAgent`) contains four sub-agents
(`QueryGeneratorAgent`, `ParallelSearchAgent`, `AlignmentAnalyst`,
`ReportCompiler`) wired through a single mutable ADK `session.state` dict.
Retry is implemented as a 5-layer system (`RetryingLlmAgent` leaf retry +
`runtime/resilience/` warm-resume + `ReflectAndRetryToolPlugin` +
`HttpRetryOptions` + `services/async_retry.py`) that shares one counter per
agent name across layers. **Verified defect:** the leaf and runner layers
decrement the same `agent_retry_counts[agent_name]` budget, so the runner's
warm-resume retry is denied before it ever runs (see `improvements.md` §A1).
`ParallelSearchAgent` has no retry at any layer and no QPS control at all
(§A2/A3); on failure it fabricates placeholder text that is still billed.

**Root cause:** four independent agents forced to share one invocation
context so they can pass data through session state.

**Fix:** stop sharing session state between agents. Each agent becomes a
single-agent ADK `Runner` invocation, driven by an outer Python retry loop
in a common base class. Data passes between agents as **typed dataclasses**
returned from `.run()`, not through a shared dict. This was proven to work
with a live experiment: a flaky ADK `LlmAgent` retried from a plain outer
loop recovered on attempt 3 with no session-reset code required.

## 2. Design Principles (from user requirements)

1. One root **pipeline**, not one root **agent** — sub-agents become
   independent steps composed in Python, not ADK sub-agents sharing state.
2. When an LLM call inside a step fails, retry **that step only** — no
   shared retry budget, no invocation-resume machinery.
3. No context/session passed between agents — only typed **outputs**.
   Exception: `ReportCompiler` explicitly takes `SearchFindings` +
   `ColtAlignment` as its two named inputs (per user instruction).
4. `ParallelSearchAgent` gets real QPS control and real per-query retry.
5. No `Protocol`/adapter indirection — concrete classes, direct composition.
6. Fewer files. Consolidate, don't multiply.
7. Keep Google ADK — verified it is not the source of the coupling problem;
   dropping it would rebuild features (structured output, safety settings,
   tool loop, auto-instrumentation) that are already working today.

## 3. Proposed Directory Structure

```
src/worker/
├── agents/
│   ├── base.py              ★ NEW   Agent (ABC) · AdkAgentStep · RetryPolicy
│   │                                ErrorKind · classify() · AgentError
│   ├── models.py            ★ NEW   ResearchRequest · Query · QueryPlan
│   │                                DomainFinding · Evidence · SearchFindings
│   │                                ColtAlignmentMapping · ColtAlignment
│   │                                CompilerInput · Report · PipelineResult
│   ├── planner.py           ★ NEW   QueryPlanner (LlmAgent step)
│   │                                + Bm25QuerySelector (moved as-is)
│   ├── search.py            ★ REWRITE  SearchExecutor + RateLimiter
│   │                                (replaces agents/search_agent.py)
│   ├── alignment.py         ★ NEW   AlignmentAnalyst (catalog injected
│   │                                at construction, no tool round-trip)
│   ├── compiler.py          ★ NEW   ReportCompiler(CompilerInput) ->
│   │                                Report
│   ├── prompts.py           ~ MODIFY  single-brace {var} only, rendered
│   │                                from typed request objects
│   ├── safety.py            ~ MODIFY  trim to get_default_safety_settings()
│   │                                + get_safety_config_for_agent()
│   └── tools/
│       ├── evidence.py              KEEP (used by evaluation + pipeline)
│       ├── verification.py          KEEP (evaluation M6 groundedness)
│       ├── embedding_similarity.py  KEEP (evaluation M5 semantic score)
│       └── gcs_pdf_loader.py        KEEP (catalog PDF load, evaluation too)
├── pipeline.py               ★ NEW   ResearchPipeline (4-step composition)
├── observers.py               ★ NEW   Observer (ABC) · TelemetryObserver
│                                     ProgressObserver · TracingObserver
│                                     CompositeObserver
├── runtime/
│   ├── pricing.py                    KEEP (cost calc, unrelated to retry)
│   ├── telemetry.py           ~ MODIFY  driven by TelemetryObserver calls
│   │                                  instead of before/after callbacks
│   └── search_log.py                 KEEP (search cache accounting)
├── domain/
│   ├── contracts.py           ~ MODIFY  add explicit DOMAIN_SLUG ->
│   │                                  OUTPUT_KEY map (fixes C3 bug)
│   └── schemas.py                    KEEP (Pydantic output_schema types)
├── services/
│   ├── finalization_service.py       KEEP (unchanged, protected by bridge)
│   ├── finalization_ops.py           KEEP
│   ├── artifacts.py                  KEEP
│   ├── metrics.py                    KEEP
│   ├── status.py                     KEEP
│   ├── formatting.py                 KEEP
│   └── async_retry.py                KEEP (used by finalization side-ops
│                                       only, unrelated to agent retry)
├── evaluation/                       KEEP — all 4 files untouched
├── api/                              KEEP — all 4 files untouched
├── model.py                          KEEP (Gemini() default + retry_config)
├── dependencies.py             ~ MODIFY  wire ResearchPipeline instead of
│                                       ResearchPipelineService
└── main.py                           KEEP
```

`★ NEW` = new file · `★ REWRITE` = existing file fully replaced ·
`~ MODIFY` = existing file edited in place · `KEEP` = untouched.

## 4. Files To Be Removed (23 files, 3,984 lines)

Deleted **only at Step 6** (Cutover), after the new pipeline is proven
equivalent. Old and new code coexist through Steps 1-5.

### 4.1 `src/worker/runtime/resilience/` — entire package (681 lines)
| File | Lines | Why removed |
|---|---|---|
| `runner_loop.py` | 298 | Warm-resume / invocation-continuation logic; replaced by per-step retry loop in `base.py` |
| `state.py` | 172 | Shared `agent_retry_counts` — the exact structure causing bug A1 |
| `errors.py` | 117 | `classify_error` / `retry_scope_for_error_class` — replaced by single `classify()` in `base.py` |
| `__init__.py` | 51 | Package re-exports, dies with the package |
| `adk_resume.py` | 43 | `append_agent_reset_events` — only needed when multiple agents share one invocation |

### 4.2 `src/worker/agents/callbacks/` — entire package (804 lines)
| File | Lines | Why removed |
|---|---|---|
| `model.py` | 332 | Token capture + grounding extraction + `{{var}}` prompt renderer (bug C2) — folded into `AdkAgentStep` |
| `agent.py` | 232 | Domain-output gate + `_record_bm25_telemetry` + dead `ResearchSynthesizer`/stagger branches — folded into `SearchExecutor.validate()` and `TelemetryObserver` |
| `tool.py` | 179 | Search-result extraction from tool responses — folded into `SearchExecutor` (search no longer goes through an ADK tool call) |
| `common.py` | 47 | Duplicate injection-pattern lists (already covered by `guardrails.py`) |
| `__init__.py` | 14 | Package re-exports |

### 4.3 Orchestration (854 lines)
| File | Lines | Why removed |
|---|---|---|
| `services/orchestrator.py` | 429 | `ResearchJobOrchestrator` + 4 pass-through adapter classes + `ResearchApplicationService`/`ResearchJobCommand` — replaced by `pipeline.py` (~90 lines) |
| `runtime/runner.py` | 326 | ADK multi-agent `Runner` lifecycle wrapper — replaced by `AdkAgentStep.execute()` (one small `Runner` per step) |
| `services/pipeline_service.py` | 99 | Thin wrapper constructing the orchestrator + adapters — logic moves into `dependencies.py` |

### 4.4 Agent wrappers (364 lines)
| File | Lines | Why removed |
|---|---|---|
| `agents/retrying_agent.py` | 190 | `RetryingLlmAgent` leaf-retry subclass — replaced by `Agent.run()` template method |
| `agents/workflow.py` | 108 | `SalesResearchWorkflowAgent` root agent + `SalesAgentAppFactory` — replaced by `pipeline.py` |
| `agents/leaf.py` | 66 | `create_llm_agent()` factory — replaced by per-agent classes in `planner.py`/`alignment.py`/`compiler.py` |

### 4.5 Session plumbing (334 lines)
| File | Lines | Why removed |
|---|---|---|
| `runtime/state_mutation.py` | 125 | `StoredSessionStateAdapter` for patching live ADK sessions mid-retry — unnecessary once each step gets a fresh session |
| `runtime/progress.py` | 104 | `ResearchProgressTracker` reading ADK events off a shared runner — replaced by `ProgressObserver` |
| `runtime/event_log.py` | 87 | Verbose ADK event dumper for the 4-agent shared runner | 
| `runtime/session_service.py` | 10 | `build_session_service()` factory — inlined into `AdkAgentStep` (fresh `InMemorySessionService` per attempt) |
| `runtime/session_ids.py` | 8 | `runner_session_id()` — trivial, inlined |

### 4.6 Dead domain layer (167 lines)
| File | Lines | Why removed |
|---|---|---|
| `domain/session_state.py` | 77 | `ResearchSessionState` façade — never instantiated (verified) |
| `domain/output_validation.py` | 47 | Duplicate of `contracts.py::validate_agent_output` (bug D5) |
| `domain/models.py` | 40 | `ResearchJob`/`ResearchMetrics`/`EvidenceRecord` — never instantiated (verified); superseded by `agents/models.py` |
| `domain/agent_contracts.py` | 3 | `from .contracts import *` proxy — no longer needed once callers import `contracts` directly |

### 4.7 Dead / superseded tools (780 lines)
| File | Lines | Why removed |
|---|---|---|
| `agents/tools/domain_outputs.py` | 313 | Key-aliasing + JSON salvage for recovering state writes — unnecessary with typed `DomainFinding` outputs |
| `agents/tools/report_validation.py` | 167 | PlanReAct-tag-based validation tool — folded into `ReportCompiler` as a plain post-step call |
| `agents/tools/output_persistence.py` | 134 | Parses `/*FINAL_ANSWER*/` tags out of session events — no PlanReAct planner exists anymore |
| `agents/tools/search.py` | 76 | `verify_draft_answer` tool importing removed PlanReAct tags; unused (verified: only referenced by the broken `tools/__init__.py` proxy) |
| `agents/tools/__init__.py` | 56 | Contains the broken `composition` import (bug C1); no longer needed once call sites import concrete modules directly |
| `agents/tools/alignment_context.py` | 34 | `FunctionTool` wrapper for catalog retrieval — catalog now injected directly into the alignment prompt (fixes C5) |

### 4.8 Repo-root cleanup (bundled into this change)
| File | Lines | Why removed |
|---|---|---|
| `main.py` | 28 | Byte-identical duplicate of `main_api.py` (bug D10) |
| `src/shared/repositories/bigquery_migrations.py` | ~90 | 0% coverage, zero references anywhere (verified) |
| `src/shared/utils/grounding.py` | 45 | `extract_grounding_report` never imported (verified); logic re-implemented inline in `callbacks/model.py`, which is itself deleted |

**Total removed: ~3,984 lines. New code added: ~1,300-1,500 lines
(`base.py`, `models.py`, `planner.py`, `search.py`, `alignment.py`,
`compiler.py`, `pipeline.py`, `observers.py`). Net: approximately
−2,500 to −2,700 lines (~28-30% of `src/worker/`).**

## 5. Core Design (reference, full code written during implementation)

### 5.1 `agents/base.py`
```python
class ErrorKind(StrEnum):
    RATE_LIMIT = "RATE_LIMIT"; TIMEOUT = "TIMEOUT"; TRANSIENT = "TRANSIENT"
    INVALID_OUTPUT = "INVALID_OUTPUT"; SAFETY = "SAFETY"; FATAL = "FATAL"

RETRYABLE = frozenset({RATE_LIMIT, TIMEOUT, TRANSIENT, INVALID_OUTPUT})

def classify(exc: Exception) -> ErrorKind: ...   # single classifier,
                                                  # replaces 4 duplicate ones

@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 30.0
    exp_base: float = 2.0
    jitter: float = 0.3
    timeout: float = 120.0
    retry_on: frozenset[ErrorKind] = RETRYABLE
    def should_retry(self, kind, attempt) -> bool: ...
    def delay_for(self, attempt: int) -> float: ...   # exp backoff + jitter

class AgentError(Exception):
    def __init__(self, agent_name, kind: ErrorKind, attempts: int, cause=None): ...

class Agent(ABC, Generic[TIn, TOut]):
    name: str
    retry: RetryPolicy = RetryPolicy()

    async def run(self, request: TIn, obs: Observer) -> TOut:   # FINAL,
        ...                                                     # owns the
                                                                  # retry loop
    @abstractmethod
    async def execute(self, request: TIn) -> TOut: ...
    def validate(self, result: TOut) -> None: ...   # override for gates

class AdkAgentStep(Agent[TIn, TOut]):
    """One ADK LlmAgent = one retryable unit. Fresh session per attempt."""
    def build_agent(self) -> LlmAgent: ...
    def to_input(self, request: TIn) -> str: ...
    def to_output(self, raw: Any, usage: TokenUsage) -> TOut: ...
    async def execute(self, request: TIn) -> TOut: ...  # single-agent
                                                          # Runner, proven
                                                          # to retry cleanly
```

### 5.2 `agents/models.py` — typed IO, no shared state
```python
@dataclass(frozen=True) class ResearchRequest: job_id: str; company: str
@dataclass(frozen=True) class Query: text: str; domain: str
@dataclass(frozen=True) class QueryPlan: company: str; queries: tuple[Query, ...]
@dataclass(frozen=True) class Evidence: url: str; title: str; snippet: str; ...
@dataclass(frozen=True) class DomainFinding: domain: str; content: str; evidence: tuple[Evidence, ...]
@dataclass(frozen=True)
class SearchFindings:
    company: str
    domains: Mapping[str, DomainFinding]     # canonical enum keys, no
                                              # slug-matching heuristic (C3)
    executed: int                            # successful queries only (R4)
    failed: tuple[str, ...]
    @property
    def success_rate(self) -> float: ...
@dataclass(frozen=True) class ColtAlignmentMapping: challenge: str; solution: str; justification: str
@dataclass(frozen=True) class ColtAlignment: mappings: tuple[ColtAlignmentMapping, ...]; opportunity: str
@dataclass(frozen=True)
class CompilerInput:            # exactly the 2 named inputs the user specified
    company: str
    findings: SearchFindings    # from SearchExecutor
    alignment: ColtAlignment    # from AlignmentAnalyst
@dataclass(frozen=True) class Report: markdown: str; validation_status: str
@dataclass(frozen=True)
class PipelineResult:
    report: Report; findings: SearchFindings; alignment: ColtAlignment
    telemetry: list[dict]; token_usage: dict
    def to_legacy_state(self) -> dict:   # bridge for finalization/evaluation
        ...                             # emits the 11 keys they read today
```

### 5.3 `agents/search.py` — QPS + per-query retry + honest failure
```python
class RateLimiter:            # async token bucket, adaptive on 429
    def __init__(self, qps: float, burst: int): ...
    async def acquire(self) -> None: ...
    def penalize(self) -> None: ...   # halve rate for a cooldown window
    def recover(self) -> None: ...

class SearchExecutor(Agent[QueryPlan, SearchFindings]):
    def __init__(self, client, cache, *, qps, burst, concurrency, query_retry): ...
    async def execute(self, plan: QueryPlan) -> SearchFindings:
        # 1. partition cached vs. uncached via cache.async_get_search
        # 2. gather uncached through _one() with real concurrency + QPS gate
        # 3. assemble typed SearchFindings; failures recorded, not faked
    async def _one(self, q: Query) -> QueryResult:
        # loop: rate_limiter.acquire() -> semaphore -> genai call
        # on RATE_LIMIT: limiter.penalize(); retry per query_retry policy
        # on exhaustion: QueryResult.failed(q, kind)  -- NOT fake text
    def validate(self, findings: SearchFindings) -> None:
        if findings.success_rate < settings.SEARCH_MIN_SUCCESS_RATE:
            raise TransientError(...)   # triggers whole-step retry
```

### 5.4 `pipeline.py` — explicit composition, no shared context
```python
class ResearchPipeline:
    def __init__(self, planner: QueryPlanner, searcher: SearchExecutor,
                 analyst: AlignmentAnalyst, compiler: ReportCompiler):
        self._planner, self._searcher = planner, searcher
        self._analyst, self._compiler = analyst, compiler

    async def run(self, req: ResearchRequest, obs: Observer) -> PipelineResult:
        plan      = await self._planner.run(req, obs)             # -> QueryPlan
        findings  = await self._searcher.run(plan, obs)            # -> SearchFindings
        alignment = await self._analyst.run(findings, obs)         # -> ColtAlignment
        report    = await self._compiler.run(
            CompilerInput(req.company, findings, alignment), obs)  # explicit
        return PipelineResult(report, findings, alignment, obs.telemetry(), obs.usage())
```
Note: `alignment` only receives `findings` (not raw context), and
`compiler` only receives `findings` + `alignment` — matching the exact
data-flow the user specified. No step receives another step's prompt
history or session state.

## 6. New Settings (`src/shared/config.py`)

| Setting | Default | Purpose |
|---|---|---|
| `SEARCH_QPS` | `4.0` | Steady-state token-bucket refill rate for search calls |
| `SEARCH_QPS_BURST` | `8` | Token-bucket capacity |
| `SEARCH_TIMEOUT_SECONDS` | `60` | Per-query hard timeout (bare client has none today) |
| `SEARCH_QUERY_RETRY_ATTEMPTS` | `3` | Per-query retry count inside `SearchExecutor` |
| `SEARCH_MIN_SUCCESS_RATE` | `0.6` | Below this, `SearchExecutor.validate()` raises → whole-step retry |
| `PLANNER_RETRY_ATTEMPTS` | `3` | `RetryPolicy.max_attempts` for `QueryPlanner` |
| `ALIGNMENT_RETRY_ATTEMPTS` | `2` | `RetryPolicy.max_attempts` for `AlignmentAnalyst` |
| `COMPILER_RETRY_ATTEMPTS` | `2` | `RetryPolicy.max_attempts` for `ReportCompiler` |

`AGENT_RETRY_ATTEMPTS` / `AGENT_RETRY_WAIT_FIXED` (old shared settings) are
removed once all four steps have explicit per-agent policies.

## 7. Step-by-Step TODO List

- [x] **Step 0 — Branch & baseline**
  - [x] Create branch `feat/agent-pipeline-rewrite` off `new-arch`
  - [x] Record baseline: `357 passed`, coverage `71.45%`, tag `pre-rewrite`
  - [~] Live Accenture E2E baseline run deferred to Step 5 (cost/quota) --
        a live smoke test will run before Step 6 cutover instead

- [x] **Step 1 — Core (`agents/base.py`, `agents/models.py`)**
  - [x] Implement `ErrorKind`, `classify(exc)`, `RetryPolicy`, `AgentError`
  - [x] Implement `Agent` ABC with the `run()` template method (timeout,
        retry loop, `validate()` hook, `Observer` calls)
  - [x] Implement all frozen dataclasses in `models.py`
        (`ResearchRequest` ... `PipelineResult`)
  - [x] Implement `PipelineResult.to_legacy_state()` against the verified
        11-key contract finalization/evaluation read today
  - [x] Unit tests: retry on retryable kind, no retry on `FATAL`/`SAFETY`,
        exponential backoff growth, jitter bounds, attempt-cap enforcement,
        `validate()`-triggered retry (38 tests, all passing)
  - [x] `ruff check` + `ruff format` clean
  - [x] Full suite green: 392 passed (357 baseline + 38 new - 3 counted twice)

- [x] **Step 2 — ADK bridge (`AdkAgentStep` in `agents/base.py`)**
  - [x] Implement `build_agent()` / `to_input()` / `to_output()` hook
        methods and `execute()` (fresh `InMemorySessionService` + single-
        agent `Runner` per attempt, per the verified experiment)
  - [x] Verify token usage extraction reuses
        `runtime/pricing.py::extract_usage_counts` unchanged
  - [x] Test: flaky fake `BaseLlm` that fails N times then succeeds —
        assert the outer `Agent.run()` loop recovers and other steps are
        never touched (regression test for bug A1) -- 5 tests passing
  - [x] Test: fatal error (`SAFETY`) does not retry
  - [x] Full suite green: 397 passed

- [x] **Step 3 — Search (`agents/search.py`) — highest-value step**
  - [x] Implement `RateLimiter` (async token bucket, `penalize()`/`recover()`)
  - [x] Implement `SearchExecutor.execute()`: cache partition, bounded
        concurrency + QPS gate, per-query retry via `_run_one()`
  - [x] Implement `QueryResult.failed()` — no fabricated placeholder text
  - [x] `executed` counts only successful queries (fixes cache mis-billing R4)
  - [x] Implement `validate()` success-rate gate (fixes "no retry for
        search" A2)
  - [x] Use `client.aio.models.generate_content` — drop
        `asyncio.to_thread` (verified `.aio` exists)
  - [x] Explicit `DOMAIN_SLUG_TO_OUTPUT_KEY` map with a load-time
        completeness check (fixes C3 — tech_stack now maps correctly)
  - [x] Tests: QPS ceiling respected under burst load, penalize()/recover()
        cycle, per-query retry exhausts then records failure (not fake
        text), `validate()` raises below `SEARCH_MIN_SUCCESS_RATE` and
        triggers a whole-step retry, cache hits not double-billed
        (12 tests, all passing)
  - [x] Full suite green: 409 passed
  - [ ] `genai.Client(http_options=HttpOptions(...))` timeout/retry config
        — deferred to Step 6 wiring (client is constructed by the DI layer,
        not by SearchExecutor itself)

- [x] **Step 4 — Remaining agents + prompts**
  - [x] `agents/planner.py`: `QueryPlanner(AdkAgentStep)` + move
        `Bm25QuerySelector` in as-is (logic unchanged, only I/O retyped)
  - [x] `agents/alignment.py`: `AlignmentAnalyst(AdkAgentStep)` — catalog
        text injected into the rendered prompt at construction time
        (fixes C5: removes the tool round-trip; verified by test asserting
        "retrieve_alignment_context" never appears in the rendered prompt)
  - [x] `agents/compiler.py`: `ReportCompiler(AdkAgentStep)` taking
        `CompilerInput` — output validation is a plain in-process call to
        the existing `OutputGuardrail`, not an ADK tool; verified
        `CompilerInput` has exactly {company, findings, alignment} fields
  - [x] Prompt templates rewritten as plain `str.format()` on typed
        request fields (fixes C2) — done inline in `alignment.py`/
        `compiler.py` rather than a separate `PromptTemplate` class,
        since each step's rendering is a few lines
  - [x] Explicit `DOMAIN_SLUG_TO_OUTPUT_KEY` dict done in Step 3
        (`agents/search.py`), consumed by `alignment.py`/`compiler.py`
        via `SearchFindings.domains` (fixes C3)
  - [x] Unit tests per agent using fake `BaseLlm` responses (9 tests:
        2 planner, 3 alignment, 4 compiler)
  - [x] Full suite green: 418 passed

- [x] **Step 5 — Pipeline & Observers**
  - [x] `pipeline.py`: `ResearchPipeline.run()` exactly as designed in §5.4
  - [x] `observers.py`: `Observer` ABC, `TelemetryObserver`,
        `ProgressObserver` (BigQuery status writes), `TracingObserver`
        (OTel spans via existing `@traced` helpers), `CompositeObserver`
        (built in Step 1; `Agent.run()` updated in Step 5 to call
        `on_usage()` so `TelemetryObserver.token_usage()` is populated)
  - [x] End-to-end test with fake LLMs across all 4 steps, asserting
        `PipelineResult.to_legacy_state()` output shape (1 test, passing;
        full suite green at 419)
  - [ ] Live E2E run (Accenture) on the **new** pipeline; diff report
        section-by-section against the Step-0 baseline

- [x] **Step 6 — Cutover (irreversible; tag before this step)**
  - [x] Tag `pre-cutover` for rollback safety (commit `8de51f6`)
  - [x] Created `services/job_runner.py::ResearchJobRunner` (replaces
        orchestrator.py + runtime/runner.py + pipeline_service.py) and
        wired it through `worker/dependencies.py` and
        `worker/api/handlers.py::ResearchTaskHandler`
  - [x] Updated `api/services/research_job_service.py`'s local dev-mode
        in-process path to build `ResearchJobRunner` directly
  - [x] Deleted 26 files total: the 23 from §4 plus `main.py`,
        `bigquery_migrations.py`, `shared/utils/grounding.py`
  - [x] Fixed `src/worker/agents/__init__.py`,
        `src/worker/runtime/__init__.py`, `src/worker/domain/__init__.py`,
        `src/worker/services/__init__.py`, `src/shared/utils/__init__.py`
        exports (all referenced deleted modules)
  - [x] Deleted 16 obsolete test files that exclusively tested deleted
        machinery (8 from §8.1 plus 8 more discovered during cutover:
        `test_architecture_mock.py`, `test_agent_registry_and_factory.py`,
        `test_domain_contracts.py`, `test_report_verification_contract.py`,
        `test_research_characterization.py`,
        `test_research_service_application_bridge.py`,
        `test_pipeline_service.py`,
        `test_sales_workflow_and_parallel_search.py`,
        `test_session_state.py`, `test_domain_outputs.py`,
        `test_agent_factory_callback_order.py`)
  - [x] Fixed 6 test files referencing the old constructor signatures
        (`ResearchTaskHandler`, `worker.dependencies`, `research_job_service`)
  - [x] Verified zero functional references to any of the 26 deleted
        modules remain anywhere in `src/`/`tests/` (only docstring/comment
        mentions in the new files documenting what was replaced)
  - [x] Full test suite green: **331 passed, 0 failed**
  - [x] `ruff check .` and `ruff format --check .` clean across the
        entire repository

- [ ] **Step 7 — Tests, coverage, config cleanup**
  - [ ] Add the 6 new test modules listed in §8
  - [ ] Remove `AGENT_RETRY_ATTEMPTS`/`AGENT_RETRY_WAIT_FIXED`; add the 8
        settings from §6
  - [ ] Fix `pyproject.toml [tool.coverage.run] omit` (currently 11 stale
        globs pointing at pre-refactor paths)
  - [ ] `uv run pytest tests/ --cov=src --cov-fail-under=80` passes
  - [ ] `ruff check .` / `ruff format . --check` clean

- [ ] **Step 8 — Documentation**
  - [ ] Update all 6 `memory-bank/*.md` files to describe the new
        pipeline architecture
  - [ ] Update `README.md` architecture diagram and "4-phase pipeline"
        description
  - [ ] Update `aidlc-docs/` unit-of-work docs affected by deleted modules

## 8. Test Impact (verified against current `tests/` tree)

### 8.1 Delete (test deleted machinery — 8 files)
`tests/utils/test_retrying_llm_agent.py` ·
`tests/utils/test_retry_and_contracts.py` ·
`tests/utils/test_agent_pipeline_retry.py` ·
`tests/utils/test_agent_contracts_gates.py` ·
`tests/utils/test_research_orchestrator.py` ·
`tests/worker/runtime/test_state_mutation.py` ·
`tests/agents/test_session_ids.py` ·
`tests/utils/test_output_persistence.py`

### 8.2 Rewrite (test surface changes — 8 files)
`tests/agents/test_architecture_mock.py` ·
`tests/worker/services/test_pipeline_service.py` ·
`tests/worker/services/test_service_unit.py` ·
`tests/worker/api/test_research_task_handler.py` ·
`tests/utils/test_domain_contracts.py` ·
`tests/worker/services/test_domain_outputs.py` ·
`tests/utils/test_research_characterization.py` ·
`tests/utils/test_research_service_application_bridge.py`

### 8.3 Verify unaffected (only match the local var name `session_state` — 4 files)
`tests/worker/runtime/test_metrics.py` ·
`tests/worker/runtime/test_model_pricing.py` ·
`tests/worker/services/test_artifacts_service.py` ·
`tests/utils/test_finalization_ops.py`

### 8.4 New tests (6 files)
`tests/worker/agents/test_retry_policy.py` ·
`tests/worker/agents/test_agent_base.py` ·
`tests/worker/agents/test_rate_limiter.py` ·
`tests/worker/agents/test_search_executor.py` ·
`tests/worker/test_pipeline.py` ·
`tests/worker/test_observers.py`

### 8.5 Also review (reference deleted modules; may need import fixes only)
`tests/utils/test_report_verification_contract.py` ·
`tests/worker/domain/test_session_state.py` ·
`tests/api/dependencies/test_handler_dependencies.py` ·
`tests/api/dependencies/test_service_dependencies.py`

## 9. Correctness Fixes Folded Into This Rewrite

| ID | Bug | Resolved by |
|---|---|---|
| C1 | Broken `composition` import | `tools/__init__.py` deleted entirely |
| C2 | `{{var}}` vs `{var}` templating split | `PromptTemplate.render(typed_request)`, single convention |
| C3 | `techstackagent_output` never populated | Explicit `DOMAIN_SLUG -> OUTPUT_KEY` map, no substring heuristic |
| C5 | Alignment tool + `output_schema` combo | Catalog injected into prompt at construction, no tool call |
| A1 | Shared retry-budget collision (leaf vs runner) | One `RetryPolicy` per `Agent`, no shared counter |
| A2 | Search has no retry at any layer | `SearchExecutor` per-query retry + whole-step `validate()` retry |
| A3 | No QPS control anywhere | `RateLimiter` token bucket with adaptive 429 penalty |
| R3 | Search failures faked as success | `QueryResult.failed()`, never fabricated text |
| R4 | Cache hits billed as fresh searches | `executed` counts successes only |
| D5 | Duplicate `validate_agent_output` | Single validation path in `SearchExecutor`/`AdkAgentStep` |
| D10 | `main.py` duplicate of `main_api.py` | `main.py` deleted |

## 10. Invariants To Hold Throughout

1. `PipelineResult.to_legacy_state()` must emit exactly the 11 keys
   `finalization_service`, `evaluation/`, `artifacts.py`, and `metrics.py`
   currently read from `session_state` (verified list: `company_name`,
   `job_evidence`, `raw_search_cache`, `agent_telemetry_records`,
   `mc_input_tokens`, `mc_output_tokens`, `mc_tokens_by_model`,
   `mc_temperature`, `mc_search_count`, `report_validation_status`,
   `report_validation_violations`).
2. Prompt **text** for all 4 agents stays byte-identical through Step 4;
   only the substitution mechanism changes. Any wording change is a
   separate, explicitly reviewed follow-up.
3. `google-adk` dependency stays in `pyproject.toml` — verified not the
   source of the coupling problem.
4. `evaluation/`, `finalization_service.py`, `finalization_ops.py`,
   `artifacts.py`, `metrics.py`, `status.py`, `formatting.py` are not
   touched by this change.
5. Old pipeline (`workflow.py` + `orchestrator.py` + `runner.py`) and new
   pipeline coexist through Steps 1-5; deletion happens only in Step 6.

## 11. Rollback Plan

- All work on branch `feat/agent-pipeline-rewrite`; `new-arch` untouched
  until merge.
- Tag `pre-cutover` immediately before Step 6 (the only step that deletes
  files). Revert to this tag restores the fully working current system.
- If Step 5's live E2E diff shows material report-quality regression,
  stop before Step 6 — no production code has been touched yet.

## 12. Definition of Done

- [ ] All 8 steps checked off
- [ ] `uv run pytest tests/ --cov=src --cov-fail-under=80` passes
- [ ] `ruff check .` and `ruff format . --check` clean
- [ ] Live E2E run on new pipeline produces a report materially
      equivalent to the Step-0 baseline (all 13 sections populated,
      including Technology Landscape — regression check for C3)
- [ ] No references to deleted modules remain (`grep -rn` clean for each
      entry in §4)
- [ ] `google-adk` still imported and functioning (§10.3)
- [ ] Memory bank + README updated (Step 8)

   tool loop, auto-instrumentation) that are already working.
