# Sales-Agent — Deep Code Review & Gap Analysis Report

> Generated 2026-08-26 — analysis only, no code changed. All findings below were
> verified by reading every source module and by executing the test suite /
> targeted repro scripts in this environment (not inferred from comments alone).

**Repo:** `Sales-Agent` · Branch `new-arch` @ `418bc05`
**Scale:** ~21,740 LOC (Python), 115 source modules, 60 test modules
**Verified state:** `357 passed, 0 failed in 9.82s` · **Coverage: 71%** (CI gate is 80% → **CI is currently red**)

---

## 1. Architecture as Implemented

```
Client / AI-Hub-UI
   └─ POST /api/v1/research/initiate
      ├─ src/api/          (APP_ROLE=api,  Cloud Run)
      │    core/{iap_auth,security,entitlements}
      │    handlers/research_handler → services/{research_job_service,cloud_tasks_service}
      │    └─ Cloud Tasks (OIDC) ──┐
      ├─ src/shared/   config · logging · otel · exceptions · middlewares
      │                repositories/{bigquery,gcs,firestore,redis,clients}
      │                utils/{guardrails,tracing,url_utils,grounding} · model_registry
      └─ src/worker/       (APP_ROLE=worker, Cloud Run)  ◄─────┘
           api/       auth · handlers · routes · health
           services/  pipeline_service → orchestrator → {artifacts, finalization, metrics, status}
           runtime/   runner · resilience/{runner_loop,state,errors,adk_resume} · pricing · telemetry · progress
           agents/    workflow → keyword → search → alignment → compiler (+callbacks, tools)
           evaluation/ section_a (LLM judge 80%) + section_b (automated 20%)
           domain/    contracts · schemas · models · session_state
```

The layering intent is genuinely good. The problems are almost entirely
**residue from the recent `new-arch` refactor** — dead paths, stale
references, and two prompt/agent wiring bugs that silently degrade report
quality.

---

## 2. Critical Bugs (P0 — verified by execution)

### 🔴 C1. `src/worker/agents/tools/__init__.py` imports a package that no longer exists
```python
# tools/__init__.py:29-33
def create_llm_agent(*args, **kwargs):
    from ..composition.leaf import create_llm_agent as _create_llm_agent   # ← no such module
```
**Verified:**
```
BROKEN: ModuleNotFoundError No module named 'src.worker.agents.composition'
```
`src/worker/agents/composition/` was deleted during the 6-layer
consolidation; the compatibility proxy was left behind. It exports cleanly
(the failure is deferred to call time), so **no test catches it**. Any
caller that resolves `create_llm_agent` off the tools package crashes at
runtime.
**Fix:** delete the proxy and its `__all__` entry; the real factory is
`agents/leaf.py::create_llm_agent`.

---

### 🔴 C2. ReportCompiler prompt uses `{{var?}}` — ADK only substitutes `{var?}`
`prompts.py::REPORT_COMPILER_PROMPT` contains **15** double-brace
placeholders. ADK's `inject_session_state` regex is `r'{+[^{}]*}+'` and
`_is_valid_state_name` runs `.isidentifier()` on the stripped name —
`{{company_name?}}` strips to `company_name` and *would* work in ADK...
**but the agent sets `include_contents="none"`**, so ADK's instruction path
is bypassed for history, and the codebase instead added a **manual regex
renderer** to compensate:

```python
# callbacks/model.py:31-65 — _render_report_prompt_template
pattern = r"\{\{" + var + r"\?\}\}"      # only handles the 14 hardcoded names
```
This is a **second, hand-rolled templating engine** duplicating ADK's
built-in one, with a hardcoded variable allow-list that must be kept in
sync with `DOMAIN_OUTPUT_KEYS` by hand. Meanwhile `ALIGNMENT_PROMPT` uses
**13 single-brace** `{var?}` placeholders (the ADK-native form). **Two
different templating conventions for two sibling agents in the same file.**

**Impact:** any new domain key is silently rendered as literal
`{{newkey?}}` text in the compiler prompt.
**Fix:** standardise on ADK-native `{var?}` for both prompts; delete
`_render_report_prompt_template` and `_inject_session_state_into_report_prompt`
(95 lines).

---

### 🔴 C3. `techstackagent_output` **never** receives real search data
`search_agent.py:252-267` maps domain keys to search domains via a fragile
substring heuristic:
```python
base_slug = key.replace("agent_output","").replace("_output","").replace("signals","_signals")
matching = [d for d in by_domain_text if d in base_slug or base_slug in d]
```
**Verified output — 11 of 12 match, 1 does not:**
```
techstackagent_output   → base_slug 'techstack'  → []   <== NO MATCH
```
The BM25 selector's domain is `tech_stack` (underscore); the contract key
strips to `techstack`. So the Technology Landscape domain **always** gets
the placeholder string:
> `"Data for techstackagent_output retrieved from research on {company}."`

The domain gate still passes (needs only 6/12), so the job completes and
the report ships a fabricated/empty Technology Landscape — one of the
**highest-weighted** evaluation dimensions (`D5`, weight 1.5) and a core
Colt selling angle.
**Fix:** replace the substring heuristic with an explicit
`DOMAIN_SLUG → OUTPUT_KEY` dict in `domain/contracts.py`.

---

### 🔴 C4. Empty model names sent to Gemini when `.env.example` is used
**Verified with `DOTENV_PATH=.env.example`:**
```
EVALUATOR_MODEL       = ''
COMPACT_SUMMARIZER    = ''
OG_HALLU_MODEL        = ''
COMPACT_ENABLED       = True
```
`config.py` defines correct fallback **properties**
(`settings.evaluator_model`, `.agent_compact_summarizer_model`,
`.output_guardrail_hallucination_model`) — but **every call site uses the
raw uppercase attribute instead**:

| Call site | Uses | Should use |
|---|---|---|
| `evaluation/service.py:161,168,69` | `settings.EVALUATOR_MODEL` | `settings.evaluator_model` |
| `agents/workflow.py:83` | `settings.AGENT_COMPACT_SUMMARIZER_MODEL` | `settings.agent_compact_summarizer_model` |
| `utils/guardrails.py:575,589,721,735` | `settings.OUTPUT_GUARDRAIL_HALLUCINATION_MODEL` | `settings.output_guardrail_hallucination_model` |

Consequences: **evaluation silently returns `empty_section_a(error=...)`
for every job** (Section A is 80% of the composite score → all reports
score ~0), and `LlmEventSummarizer(Gemini(model=""))` is constructed on
every run with compaction enabled. Tests pass only because
`tests/settings_env.py` explicitly sets these three vars.
**Fix:** switch the 8 call sites to the properties, or drop the properties
and make the fields default to `LLM_MODEL`/`SEARCH_MODEL` via a
`model_validator`.

---

### 🔴 C5. `AlignmentAnalyst` sets both `output_schema` **and** a tool
```python
create_llm_agent(name="AlignmentAnalyst", output_schema=ColtAlignmentOutput,
                 tools=[alignment_context_tool], include_contents="none")
```
ADK ≥2.1 supports this combination (`llm_agent.py:357`), but it forces a
two-turn flow (tool call → schema-constrained final turn) while
`include_contents="none"` **discards the tool result from the next turn's
context**. The prompt's mandated workflow ("1. Call
`retrieve_alignment_context()` … 2. Synthesise") is therefore not reliably
satisfiable. The catalog text is already available synchronously via
`get_alignment_context()` and is **pre-warmed at worker startup**
(`main.py:57`).
**Fix:** drop the tool; inject the catalog text directly into the
instruction at agent-construction time. Removes a whole LLM round-trip per
job (latency + tokens) and deletes `tools/alignment_context.py`.

---

### 🔴 C6. CI coverage gate will fail
`.gitlab-ci.yml:559` → `--cov-fail-under=80`. **Measured TOTAL: 71%.** The
`pyproject.toml` `[tool.coverage.run] omit` list still points at **deleted
paths** (`*/worker/agents/sales/prompts/*`, `*/worker/finalization/*`,
`*/worker/run/*` …) — every one of those globs is now a no-op, so
previously-excluded modules are counted. The memory bank claims the gate
passes; it does not.

---

## 3. Robustness Gaps (P1)

### R1. Redis cache uses `KEYS` on a shared Memorystore cluster
`redis_repository.py:159,179,230,240` — four methods call
`self.client.keys(pattern)`. `KEYS` is **O(N) over the entire keyspace and
blocks the single-threaded Redis event loop**. On a cluster shared with
Translation (`translation-cache:` prefix), this stalls *both* services.
**Fix:** use `SCAN`/`scan_iter` with `count=`, or maintain a per-company
`SET` index. (Note: none of these four methods are currently called by the
pipeline — see D4 — so this is cheapest to fix by deletion.)

### R2. Local dispatch does a 1-hour blocking HTTP call inside the request handler
`cloud_tasks_service.py:88` — `requests.post(url, timeout=3600.0)` runs
**synchronously inside an `async def` FastAPI route** (`initiate_research`).
It blocks the entire event loop for the full pipeline duration. Only
affects `IS_LOCAL` + `http://` worker URL, but it makes local E2E testing
single-threaded and masks concurrency bugs.
**Fix:** `asyncio.to_thread(...)`, or fire-and-forget with a short connect
timeout.

### R3. Search failures are swallowed as fake success
`search_agent.py:105-115` — on exception the query returns
`{"text": f"Search unavailable for query: {query}"}`. That string is then
treated as legitimate domain content, counted in `search_count` (line 282,
`len(queries)` — **not** the number of *successful* searches), and
**billed** at $35/1k. A total Gemini outage produces a full-priced report
of "Search unavailable…" placeholders that passes the 6/12 gate.
**Fix:** track `failed_queries` separately; exclude from `search_count`;
fail the gate if success rate < threshold.

### R4. `search_count` double-write races the cost record
`state["search_count"] = len(queries)` and
`state["mc_search_count"] = len(queries)` (lines 282-283) overwrite the
incrementally-maintained counter from `record_search_query`
(`search_log.py:61`), which only counts *executed* (cache-miss) searches.
Cache hits are therefore **billed as fresh searches** — defeating the
entire purpose of the 7-day cache. With 30 queries at $35/1k that's $1.05
charged even on a 100% cache hit.

### R5. Redis and Firestore caches both active, storing different things
The pipeline writes search results to **Redis** (`search_agent.py:215`)
*and* flushes the same searches to **Firestore**
(`finalization_ops.py::run_search_log_op` →
`FirestoreSearchCacheRepository`). Only Redis is ever read.
`SEARCH_CACHE_BACKEND` (`"redis"|"firestore"|"none"`) is defined in config
and **never read anywhere in `src/`**. Two datastores, two write paths, one
dead read path.

### R6. Module-global mutable caches are process-wide and unbounded
- `gcs_pdf_loader.py:86` `_extracted_catalog_text` and `:137`
  `_colt_cache_name` — never invalidated; a Gemini context cache has a
  3600s TTL but `_colt_cache_name` is cached forever, so after 1h every
  call references an expired cache handle.
- `clients.py` singletons hold a `threading.Lock` acquired on *every*
  getter call (not double-checked) — minor contention on the hot
  `get_genai_client()` path in `_execute_single_search`.

### R7. `_handle_failure` string-matches on exception text
`orchestrator.py:300` —
`if "GeneratorExit" in error_msg or "TaskGroup" in error_msg`. Brittle;
will break on any ADK/Python message change. Use exception types.

### R8. Broad `except Exception` with `pass`/`debug` — 40+ occurrences
Notably `search_agent.py:131` (`except Exception: pass` when parsing
generator output → silently falls through to generic default queries), and
~20 `except Exception as e: logger.debug(...)` blocks in callbacks. Real
failures are invisible at INFO level.

### R9. `process_research_background` has a no-op try/except
```python
# pipeline_service.py:98-99
except Exception:
    raise
```
Dead code — remove.

### R10. Fail-open on missing catalog in local mode
`gcs_pdf_loader.py:102` falls back to `COLT_CATALOG_HARDCODED` — a 74-line
**hardcoded snapshot** of Colt's portfolio with hard facts (NPS 75,
38,000km fiber, 6,000 employees, Net Zero 2045). This will drift from the
real PDF and there is no staleness marker.

---

## 4. Duplication (P1)

| # | Duplication | Locations | Fix |
|---|---|---|---|
| **D1** | `company_key()` + `query_hash()` — **byte-identical** sha256[:16] implementations | `redis_repository.py:23-31`, `firestore_repository.py:44-52`, third `query_hash` in `search_log.py:29-31` | One `shared/utils/hashing.py` |
| **D2** | **Two complete pricing registries** | `shared/model_registry.py` (`ModelRegistry`/`ModelInfo`/`PricingTier`, Pydantic, `@lru_cache`) **and** `worker/runtime/pricing.py` (`ModelPricing`/`load_pricing_registry`, dataclass, `@lru_cache`) — both parse the *same* `pricing_catalog.json`, both hardcode the same fallback rates, and `pricing.py:130` falls back *into* `model_registry` | Delete `load_pricing_registry`; have `pricing.py` consume `ModelRegistry` |
| **D3** | `normalize_model_name()` (pricing.py:74) ≡ `normalize_model_id()` (model_registry.py:16) — identical bodies | Keep one |
| **D4** | `RedisSearchCacheRepository` and `FirestoreSearchCacheRepository` implement the same 5-method interface (`get/set/count/get_cached_query_hashes/get_searches_for_company`); Redis has **sync + async** variants of all 5 (10 methods) — **only `async_get_search`/`async_set_search` are ever called** | Delete 8 unused methods; extract a `SearchCachePort` Protocol |
| **D5** | `validate_agent_output` defined **twice** with different logic | `domain/contracts.py:121` and `domain/output_validation.py:15` — `runner_loop.py` imports the latter, `callbacks/agent.py` imports the former | Merge |
| **D6** | `_build_evidence_block` (guardrails.py:772) ≡ `evidence_to_block` (evidence.py:160) — same rendering, same `max_chars` truncation loop | Keep `evidence_to_block` |
| **D7** | `with_retry` / `with_retry_sync` (`services/async_retry.py`) duplicate the sync/async halves of the same algorithm; also overlaps `GEMINI_RETRY_*` + `RetryingLlmAgent` + `resilience/state.py` + ADK `ReflectAndRetryToolPlugin` — **five** retry mechanisms | Consolidate/document the layering |
| **D8** | `agent_contracts.py` is a 3-line `from .contracts import *` star-proxy; `session_state.py`, `output_validation.py`, `resilience/*` import through it while `callbacks/agent.py`, `retrying_agent.py` import `contracts` directly | Delete the proxy, repoint 6 imports |
| **D9** | Prompt-injection pattern lists duplicated: `_QUERY_INJECTION_PATTERNS`/`_SNIPPET_INJECTION_SIGNALS` (`callbacks/common.py`, 7+6 entries) vs `_JAILBREAK_PATTERNS` (`guardrails.py`, 14 regexes) — overlapping coverage, different engines (substring vs regex) | Single source in `guardrails.py` |
| **D10** | `main.py` is a byte-for-byte duplicate of `main_api.py` except `PORT` default (`8080` vs `settings.PORT`) | Delete `main.py` |
| **D11** | `Dockerfile.api` and `Dockerfile.worker` differ only in `APP_ROLE` and `CMD` (76 of 78 lines identical); a third legacy `Dockerfile` also exists | One Dockerfile + build arg |
| **D12** | Asset triplication — **verified identical md5**: `ColtProductCatalog.pdf` at repo root, `assets/`, `data/` (`d221a789…`); `pricing_catalog.json` in `assets/` + `data/` (`b14708fe…`); plus a 4th copy in `.local-tmp/assets-cache/`. `config.py` has a **3-level fallback chain** (`assets_root_path` → `pricing_catalog_path` → `colt_catalog_path`) searching 4 directories each, purely to accommodate this | One canonical `assets/` dir |
| **D13** | Domain lists repeated 5×: `Bm25QuerySelector.DOMAIN_LIMITS`, `DOMAIN_OUTPUT_KEYS`, `_render_report_prompt_template.template_vars`, `ALIGNMENT_PROMPT` placeholders, `REPORT_COMPILER_PROMPT` placeholders | Derive all from `contracts.py` |
| **D14** | `MIN_DOMAIN_OUTPUTS_REQUIRED = 6` (contracts.py:56) duplicates `settings.RESEARCH_MIN_DOMAIN_OUTPUTS = 6`; `TOTAL_BUDGET = 30` (keyword_agent.py:48) duplicates `settings.TOTAL_KEYWORD_BUDGET = 30` — **and neither class constant is ever used**, both are dead | Delete constants |

---

## 5. Dead / Unnecessary Code (P2)

| Item | Evidence |
|---|---|
| `agents/tools/search.py` (76 lines) — `verify_draft_answer`, `verify_draft_answer_tool`, `SEARCH_TOOL_NAME` | Only imported by `tools/__init__.py` (itself broken). No agent registers this tool. Imports `FINAL_ANSWER_TAG`/`REPLANNING_TAG` from the **removed** PlanReAct planner |
| PlanReAct residue after "full removal" | `plan_re_act_planner` imported in `report_validation.py:7`, `output_persistence.py:8`, `search.py:7`. `output_persistence.py` (134 lines) exists **solely** to parse `/*FINAL_ANSWER*/` tags no agent emits. `report_validation.py:102,138` instructs the model to "Emit /*FINAL_ANSWER*/" and "Use /*REPLANNING*/" — meaningless without a planner |
| `callbacks/agent.py:77-93` — 1-5s random stagger for `parallel_researchers` | The 9 agent names listed (`FirmographicsGeographicAgent`, `SignalsOrchestrator`, …) **do not exist**. Dead branch |
| `callbacks/agent.py:196` — `if agent_name == "ResearchSynthesizer": _enforce_domain_outputs(...)` | `ResearchSynthesizer` was deleted. **The domain gate never fires from the callback**; only `search_agent.py:286` enforces it |
| `runtime/resilience/adk_resume.py`, `build_retry_continuation_message` | Cold-retry path requires `requires_cold_retry()` → only true on the literal phrase `"contents are required"` |
| `shared/repositories/bigquery_migrations.py` | **0% coverage; zero references** anywhere in `src/`, `tests/`, or `scripts/` |
| `shared/utils/grounding.py` (45 lines, 27% cov) | `extract_grounding_report`/`GroundingReport` never imported; `callbacks/model.py:206-292` reimplements the same logic inline |
| `agents/safety.py` — `get_business_research_safety_settings`, `is_safety_block`, `log_safety_event`, `create_safety_summary`, `format_safety_ratings`, `analyze_safety_block` (~130 of 191 lines) | Only `get_safety_config_for_agent` + `get_default_safety_settings` are used. `get_safety_config_for_agent` ignores its `max_output_tokens` param → **`AGENT_MAX_OUTPUT_TOKENS=65535` is configured but never applied** |
| `report_validation.py:20` `aggregate_raw_search_cache` | Docstring literally says "Deprecated: use aggregate_job_evidence" |
| `evidence.py:87` `_legacy_raw_cache_entries` | Migration shim for `raw_search_cache_*` keys nothing writes. Contains a genuine bug: `key.split("_")[3] if len(...)>3 else ""` — the ternary binds to the wrong operand |
| `guardrails.py` (835 lines) | Docstring: only `check_strategic_brief_format` is on the "active blocking path". `validate()` (line 826) calls exactly one check. Two full LLM hallucination-checkers (`:575`, `:721`) are dead |
| `orchestrator.py:344-429` — 4 adapter classes (86 lines) | `BigQueryStatusAdapter`/`AdkRunnerAdapter`/`GcsArtifactAdapter`/`FinalizationAdapter` are pure pass-throughs to objects that **already** satisfy the Protocols |
| `ResearchApplicationService` (orchestrator.py:327) + `ResearchJobCommand` | Single method forwarding 4 args to `orchestrator.run` |
| `domain/models.py` — `ResearchJob`, `ResearchMetrics`, `EvidenceRecord` | Never instantiated; the codebase uses raw dicts throughout |
| `domain/session_state.py` — `ResearchSessionState` | Never instantiated |
| `api/main.py:72-97` — `_init_bigquery`, `_init_gcs`, `get_gcp_exporters`, `get_gcp_resource`, `maybe_set_otel_providers` | Empty/no-op "compatibility hooks" existing only so `conftest.py:121-123` can monkeypatch them. Worker's `main.py` has none of this |
| `api/core/iap_auth.py:280` `require_group` | Docstring: "Deprecated for production use" |
| 12 stale tracked files deleted on disk but not in git | `CHANGES_SUMMARY.md`, `IMPLEMENTATION_GUIDE.md`, `QUICK_REFERENCE.md`, `REFACTOR.md`, `TEST_GUIDE.md`, `research.md`, `run_local_research_sephora.py`, `test_query_generator_fully_real_e2e.py`, `tests/functional_ado_tests.py`, `tests/services/…`, `tests/utils/test_plan_react_tool_enforcement_callbacks.py`. `git ls-files` still lists `src/worker/agents/sales/tools/gcs_pdf_loader.py` |
| Committed artifacts | `.coverage` (77KB), `out/latest/app.log`, `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`, `ColtProductCatalog.pdf` (283KB) at repo root |
| `pyproject.toml` | `name = "colt_ai"`, `description = "Colt-AI Data Ingestion and Crawling System"` — wrong project. `omit` list references 11 deleted paths |

---

## 6. Optimisation Opportunities (P2)

| # | Opportunity | Detail |
|---|---|---|
| **O1** | **Eliminate the alignment tool round-trip** | Injecting the pre-warmed catalog text into `ALIGNMENT_PROMPT` removes one full LLM turn per job (see C5) |
| **O2** | **Fix cache accounting → real cost savings** | R4 means the Redis cache currently saves latency but **not money**. Fixing `search_count` to count only executed searches converts a 7-day cache hit from $1.05 to $0.00 |
| **O3** | `asyncio.to_thread` per search, unbounded threads | `search_agent.py:63` spawns a thread per query, bounded only by `SEARCH_CONCURRENCY_LIMIT=8` semaphore *inside* the thread. Prefer the genai **async** client (`client.aio.models.generate_content`) — removes the thread pool entirely |
| **O4** | Redis `KEYS` → `SCAN` | See R1 |
| **O5** | BigQuery `get_status` runs a full-table `ORDER BY updated_at DESC LIMIT 1` | Called on **every status poll**, plus twice per task in `ResearchTaskHandler.handle` (lines 64 and 88), plus in `get_pdf_report` and `submit_feedback`. `get_requests_by_status` uses `ROW_NUMBER() OVER (PARTITION BY …)` over the whole table. No partitioning/clustering configured. At poll-every-2s this is expensive |
| **O6** | `update_status` does a read-modify-write | `_get_metadata_dict` (a full query) then an `UPDATE` — non-atomic, races between the progress tracker and the orchestrator |
| **O7** | `upload_agent_artifacts` uploads serially | `artifacts.py:46-59` — sequential `await asyncio.to_thread` in a for-loop. Use `asyncio.gather` |
| **O8** | `state_delta` copies all 12 domain payloads | `search_agent.py:296-298` — plus `job_evidence`, `search_query_records`. Then `orchestrator.py:184` does `session_state["raw_search_cache"] = session_state["job_evidence"]` — **the entire evidence list is duplicated in state and uploaded twice to GCS** in `raw_data.json` |
| **O9** | `Bm25QuerySelector._deduplicate_queries` is O(n²) | Fine at n=30-60; note the limit |
| **O10** | Full session state serialized to GCS | `artifacts.py:37` `upload_json(job_id, session_state)` dumps *everything* incl. telemetry snapshots, per-agent BM25 arrays, evidence ×2 |
| **O11** | `ContextCacheConfig(ttl_seconds=3600)` + `EventsCompactionConfig` both enabled | With `include_contents="none"` on all 4 agents there is no cross-turn history to compact — compaction pays for an `LlmEventSummarizer` (with an **empty model name**, C4) for no benefit |
| **O12** | `onnxruntime` + `transformers` (~500MB) shipped for a disabled feature | `EVAL_EMBEDDING_ENABLED` defaults to `False`; `models/all-MiniLM-L6-v2/onnx/` must be present. Make it an optional extra |

---

## 7. Security & Config Findings

| # | Finding |
|---|---|
| **S1** | **`CORS_ALLOW_ORIGINS=["*"]` + `CORS_ALLOW_CREDENTIALS=True`** (`config.py:152-153`, applied `api/main.py:145`). Starlette will not send credentials with `*`, so this is *broken* rather than exploitable — but combined with the `colt_session` cookie it signals unreviewed config. Pin to the Hub origin |
| **S2** | `samesite="strict"` on `colt_session` (`auth.py:63`) will **drop the cookie on cross-site navigations** from the AI-Hub UI under Architecture B. `lax` is the correct choice for a same-origin-proxied SPA |
| **S3** | `iap_auth.py:227` — `logger.warning("IAP verified claims: %s", claims)` logs the **entire decoded JWT** (email, groups, sub) at WARNING on every request |
| **S4** | `WORKER_SKIP_OIDC_VERIFICATION` is a single boolean that fully disables worker auth. Guard it with `IS_LOCAL` too |
| **S5** | JWT is HS256 with a shared `SECRET_KEY` and **no `iss`/`aud` claims** (`security.py:66-71`), verified without `options={"require": [...]}` |
| **S6** | `.env.api.local` / `.env.worker.local` (4.5KB each) sit in the working tree with real sandbox config. `.gitignore` covers `.env` but **not** `.env.*.local` |
| **S7** | 3 `.env` files × ~4.5KB with near-identical content; `.env.example` leaves 3 model vars blank (C4) |
| **S8** | `_PII_PATTERNS` includes `ipv4` and `passport` (`\b[A-Z]{1,2}\d{6,9}\b`) applied to `company_name` — the passport regex matches ordinary product/ticker strings. High false-positive risk on the API input gate |
| **S9** | `_execute_query` uses parameterized queries correctly ✅ but table refs are f-string-interpolated from settings — acceptable (config-controlled), worth a comment |
| **S10** | `Dockerfile.api`/`.worker` — `COPY . .` with no non-root `USER`. Containers run as root; `.dockerignore` excludes `data/` but not `assets/`, `.env.*.local`, `out/`, `.local-tmp/` |

---

## 8. Testing Gaps

- **Coverage 71% vs 80% gate** → CI red (C6).
- **Zero coverage of the failing paths**: the domain-key mismatch (C3), the
  broken proxy (C1), and the empty-model-name bug (C4) are all invisible
  because `tests/settings_env.py` sets the vars the real `.env.example`
  leaves blank. **Tests are configured more correctly than production.**
- Lowest-coverage modules are the highest-risk ones: `callbacks/tool.py`
  **16%**, `callbacks/agent.py` **17%**, `evaluation/section_b.py` **16%**,
  `verification.py` **19%**, `evaluation/service.py` **30%**,
  `runtime/runner.py` **32%**, `redis_repository.py` **47%**.
- No integration test exercises `SalesResearchWorkflowAgent` end-to-end
  with a fake ADK runner.
- `tests/ado_evidence_generator.py` (314 lines) is tooling, not a test, but
  lives in `tests/`.
- `[tool.coverage.run] omit` silently excludes nothing (all 11 globs
  stale) — worth deciding intentionally.

---

## 9. Documentation Drift

| Claim | Reality |
|---|---|
| memory-bank/progress.md: "**361 tests passed**", "Clean ruff across 213 files" | 357 tests; 115 src + 60 test files |
| memory-bank: "Test suite coverage maintained above the 80% CI gate" | **71%** |
| memory-bank/techContext.md: "Gemini 2.5 Pro / 2.5 Flash", `GEMINI_MODEL`/`SEARCH_AGENT_MODEL`/`EVALUATOR_MODEL` | Now `gemini-3.5-flash` via `LLM_MODEL`/`SEARCH_MODEL` |
| memory-bank/systemPatterns.md: "`runtime/` … `resilience.py`" | It is a `resilience/` **package** of 4 modules |
| memory-bank: "PlanReAct Removal … Removed all PlanReActPlanner dependencies … across the entire codebase" | 3 modules still import from `plan_re_act_planner`; prompts still emit its tags |
| README/brief: "top 40 queries", "BM25 ranking" | `TOTAL_KEYWORD_BUDGET=30`; `Bm25QuerySelector._compute_bm25_score` is a **hand-written keyword-boost heuristic**, not BM25 — the real `rank_bm25.BM25Okapi` is only used in `verification.py` |
| README: "`AlignmentAnalyst` … Gemini context-cached Colt catalog" | `get_or_create_colt_context_cache` is defined but **never called** |
| `pyproject.toml` name/description | "Colt-AI Data Ingestion and Crawling System" |

---

## 10. Prioritised Remediation Plan

**Phase 1 — Correctness (do first; ~1 day, all mechanical)**
1. **C3** — explicit `DOMAIN_SLUG → OUTPUT_KEY` map; restores Technology
   Landscape.
2. **C4** — repoint 8 call sites to the fallback properties; restores
   evaluation.
3. **C1** — delete the `composition` proxy from `tools/__init__.py`.
4. **C2** — unify prompts on ADK-native `{var?}`; delete the 95-line
   hand-rolled renderer.
5. **R4/R3** — count only *executed, successful* searches into
   `search_count`.
6. **C5** — inline the catalog into `ALIGNMENT_PROMPT`; drop the tool.
7. Add regression tests for each of the above (also lifts coverage toward
   the gate).

**Phase 2 — Dead-code purge (~1 day, large net deletion, low risk)**
`tools/search.py` · `output_persistence.py` · `bigquery_migrations.py` ·
`utils/grounding.py` · `domain/models.py` · `domain/session_state.py` ·
`domain/agent_contracts.py` · `main.py` · the 4 orchestrator adapters +
`ResearchApplicationService` · dead `safety.py` helpers · dead guardrail
LLM checkers · `callbacks/agent.py` stagger + `ResearchSynthesizer` branch
· 8 unused Redis methods · dead constants (`TOTAL_BUDGET`,
`MIN_DOMAIN_OUTPUTS_REQUIRED`) · `git rm` the 12 stale tracked files ·
untrack `.coverage`/`out/`/caches. **Estimate: −2,000 to −2,500 LOC
(~10-12%).**

**Phase 3 — De-duplication (~1 day)**
`shared/utils/hashing.py` (D1) · collapse the two pricing registries
(D2/D3) · pick **one** search-cache backend and honour
`SEARCH_CACHE_BACKEND` (D4/R5) · merge the two `validate_agent_output`
(D5) · single evidence-block renderer (D6) · one Dockerfile (D11) · one
canonical `assets/` dir and collapse the `config.py` fallback chains
(D12) · derive all domain lists from `contracts.py` (D13).

**Phase 4 — Robustness & performance (~2 days)**
`SCAN` over `KEYS` (R1) · async genai client (O3) · `asyncio.gather` for
artifacts (O7) · non-blocking local dispatch (R2) · typed exception
handling in `_handle_failure` (R7) · atomic BigQuery status update (O6) ·
partition/cluster `research_requests` (O5) · stop duplicating
`job_evidence` into `raw_search_cache` (O8).

**Phase 5 — Security & docs (~0.5 day)**
Pin CORS (S1) · `samesite="lax"` (S2) · stop logging full JWT claims (S3) ·
gate OIDC skip behind `IS_LOCAL` (S4) · add JWT `iss`/`aud` (S5) ·
`.gitignore` `.env.*.local` (S6) · non-root Docker `USER` + tighten
`.dockerignore` (S10) · fix `pyproject.toml` metadata and the stale `omit`
list · **update all six memory-bank files** to match reality.

---

## Bottom Line

The architecture is sound and the intent behind the `new-arch` refactor is
right. But the refactor **deleted implementations without deleting their
references**, leaving a broken import (C1), a dead domain-gate callback,
three modules importing a removed planner, and — most seriously — **three
silent quality regressions that no test can see** because the test env is
configured better than production: the Technology Landscape domain is
always placeholder text (C3), evaluation returns a zero score for every
job (C4), and the report compiler's data injection depends on a
hand-maintained variable allow-list that duplicates ADK's own templating
(C2).

The single highest-leverage action is **Phase 1**: six mechanical fixes
that restore report quality and the evaluation signal. Phases 2-3 then
remove roughly 10-12% of the codebase with essentially no behavioural
risk, which will also close most of the 71% → 80% coverage gap by
shrinking the denominator.
