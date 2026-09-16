#!/usr/bin/env python
"""Prove the agents' tenacity retry loop actually loops -- offline.

This drives the REAL `Agent.run()` retry machinery for all four pipeline
steps (QueryPlanner, SearchExecutor, AlignmentAnalyst, ReportCompiler),
built by the REAL production factory `build_research_pipeline()`, and
asserts the observed attempt counts, error classifications and
exponential-backoff delays against expected values. Every mismatch is
reported and sets a non-zero exit code.

Why not scripts/local_core_loop.py
----------------------------------
That script's stated contract is the opposite of what a retry test needs:
"every LLM call is the real one" / "nothing above the storage line is
mocked". Concretely it cannot do this job --

  * it raises SystemExit if service_account.json is missing, so it needs
    real credentials and a real project even to start;
  * provoking a genuine RESOURCE_EXHAUSTED means burning real quota, and
    the result is not reproducible;
  * the backoff *shape* is unreachable from its 26 CLI flags -- only
    max_attempts and timeout are wired, so initial_delay/max_delay/
    exp_base/jitter come from defaults no flag can touch; and
  * with the production jitter the delays are random, so there is nothing
    stable to assert.

So this is a separate script. It reuses local_core_loop.py's two good
ideas -- freeze the environment before importing anything from `src`, and
record the pipeline through an Observer -- and drops the rest.

What is faked, and what is emphatically not
-------------------------------------------
Faked: the model call (per-scenario, so a failure is deterministic and
free) and `asyncio.sleep` inside the retry loop (so a 345s production
backoff schedule is asserted in milliseconds).

Real: `Agent.run()`'s tenacity loop, `classify()`, `RetryPolicy`,
`_PolicyWait` including its Retry-After handling, the per-attempt
`asyncio.wait_for` timeout, the wall-clock step budget, the Observer
callbacks, and -- in `--mode adk` -- `AdkAgentStep.execute()`'s
fresh-session-per-attempt ADK Runner.

Named verify_* rather than test_* on purpose: CI runs `pytest` with no
path argument, so a module matching pytest's `test_*.py` pattern here
would be collected and imported as a test module, and this file's
bootstrap_env() guard would abort collection for the entire suite.
(pyproject's testpaths now pins collection to tests/ as well.)

Usage
-----
  # Everything (both modes + the production-config audit)
  python scripts/test_retry_loop.py

  # Just the loop-level scenarios, verbose
  python scripts/test_retry_loop.py --mode loop -v

  # Sleep for real, to watch the backoff in wall-clock time (slow)
  python scripts/test_retry_loop.py --mode loop --real-sleep

  # Audit the wired production policies only
  python scripts/test_retry_loop.py --mode config
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Environment bootstrap
#
# src.shared.config builds a module-level `settings` singleton at import
# time, so every env var has to be in place before the first `src.*`
# import. Unlike local_core_loop.py this script needs NO credentials and
# makes NO network calls: get_genai_client() passes project and location
# explicitly, and google-genai only resolves ADC when `project` is empty,
# so the client is constructed lazily and never used.
# ---------------------------------------------------------------------------

_ENV: dict[str, str] = {
    "DOTENV_DISABLE": "1",
    "IS_LOCAL": "true",
    "DEBUG": "false",
    "LOG_LEVEL": "WARNING",
    "APP_NAME": "sales-agent-retry-loop-test",
    "APP_VERSION": "0.0.0-test",
    "API_PREFIX": "/api/v1",
    "HOST": "127.0.0.1",
    "PORT": "0",
    "WORKERS": "1",
    "AGENT_EVENT_LOG_VERBOSE": "false",
    "BIGQUERY_DATASET": "unused_local",
    "BIGQUERY_TABLE": "unused_local",
    "BIGQUERY_COST_ATTRIBUTION_TABLE": "unused_local",
    "BIGQUERY_AGENT_TELEMETRY_TABLE": "unused_local",
    "BIGQUERY_USER_FEEDBACK_TABLE": "unused_local",
    "GCS_BUCKET_NAME": "unused-local",
    "SECRET_KEY": "retry-loop-test-not-a-real-secret",
    "ALGORITHM": "HS256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
    "GOOGLE_CLOUD_PROJECT": "retry-loop-test",
    "GOOGLE_CLOUD_QUOTA_PROJECT": "retry-loop-test",
    "GOOGLE_CLOUD_LOCATION": "global",
    "VERTEX_AI_LOCATION": "global",
    "GOOGLE_GENAI_USE_VERTEXAI": "true",
    "LLM_MODEL": "gemini-3.5-flash",
    "SEARCH_MODEL": "gemini-3.5-flash",
    "SEARCH_CACHE_BACKEND": "none",
    "REPORT_COMPILER_BM25_GATE_ENABLED": "false",
    # Isolate the outer tenacity loop: google-genai's own in-client HTTP
    # retry would otherwise multiply against it. 429 is already excluded
    # from GEMINI_RETRY_STATUS_CODES in production for the same reason.
    "GEMINI_RETRY_ATTEMPTS": "1",
    # No telemetry exporters for an offline run.
    "OTEL_ENABLED": "false",
    "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED": "false",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "",
    "OTEL_RESOURCE_ATTRIBUTES": "",
}


def bootstrap_env() -> None:
    if "src.shared.config" in sys.modules:  # pragma: no cover - guard
        raise SystemExit(
            "src.shared.config was imported before bootstrap_env(); the settings "
            "singleton is already frozen with the wrong values."
        )
    os.environ.update(_ENV)
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)


bootstrap_env()

# ruff: noqa: E402 - every src import must follow bootstrap_env()
import asyncio  # noqa: E402

from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.genai import types as genai_types  # noqa: E402

from src.worker.agents.base import (  # noqa: E402
    AgentError,
    ErrorKind,
    RetryPolicy,
)
from src.worker.agents.models import (  # noqa: E402
    ColtAlignment,
    ColtAlignmentMapping,
    CompilerInput,
    DomainFinding,
    Evidence,
    Query,
    QueryPlan,
    ResearchRequest,
    SearchFindings,
)
from src.worker.observers import Observer  # noqa: E402
from src.worker.services.task_attempt import (  # noqa: E402
    QUEUE_RETRYABLE_KINDS,
    JobPhase,
    TaskAttempt,
    should_redispatch,
)

# ---------------------------------------------------------------------------
# Instant backoff
#
# RetryPolicy.build_retrying() is the single factory every retry loop in
# the pipeline goes through, so patching it at the CLASS level (RetryPolicy
# uses __slots__, so per-instance patching is impossible) swaps the sleep
# for a recorder without touching src/. tenacity's AsyncRetrying.copy()
# takes the same kwargs as its constructor.
#
# AsyncRetrying does a bare `await self.sleep(...)`, so the replacement MUST
# be `async def` -- a plain function raises
# "object NoneType can't be used in 'await' expression".
# ---------------------------------------------------------------------------

SLEEPS: list[float] = []
_REAL_SLEEP = False


async def _recording_sleep(seconds: float) -> None:
    SLEEPS.append(seconds)
    if _REAL_SLEEP:
        await asyncio.sleep(seconds)


_ORIGINAL_BUILD_RETRYING = RetryPolicy.build_retrying


def _build_retrying_with_recorder(self: RetryPolicy, **kwargs):
    # **kwargs rather than a mirrored signature, so this seam survives
    # build_retrying() growing a parameter without silently breaking.
    return _ORIGINAL_BUILD_RETRYING(self, **kwargs).copy(sleep=_recording_sleep)


def install_sleep_recorder() -> None:
    RetryPolicy.build_retrying = _build_retrying_with_recorder  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Recording observer
# ---------------------------------------------------------------------------


class RecordingObserver(Observer):
    """Captures the whole retry timeline.

    `on_retry`'s `delay` is the exact value the loop is about to sleep, so
    the backoff schedule is asserted on reported intent rather than on
    measured wall-clock -- which matters on Windows, where the
    ProactorEventLoop quantises asyncio.sleep to ~15.6ms.
    """

    def __init__(self) -> None:
        self.starts: list[tuple[str, int]] = []
        self.retries: list[tuple[str, int, ErrorKind, float]] = []
        self.successes: list[tuple[str, int, float]] = []
        self.failures: list[tuple[str, int, ErrorKind, BaseException]] = []

    def on_start(self, agent_name: str, attempt: int) -> None:
        self.starts.append((agent_name, attempt))

    def on_retry(
        self, agent_name: str, attempt: int, kind: ErrorKind, delay: float
    ) -> None:
        self.retries.append((agent_name, attempt, kind, delay))

    def on_success(self, agent_name: str, attempt: int, seconds: float) -> None:
        self.successes.append((agent_name, attempt, seconds))

    def on_failure(
        self, agent_name: str, attempt: int, kind: ErrorKind, exc: BaseException
    ) -> None:
        self.failures.append((agent_name, attempt, kind, exc))

    def attempts_for(self, agent_name: str) -> int:
        return len([s for s in self.starts if s[0] == agent_name])

    def delays_for(self, agent_name: str) -> list[float]:
        return [round(r[3], 6) for r in self.retries if r[0] == agent_name]

    def kinds_for(self, agent_name: str) -> list[ErrorKind]:
        return [r[2] for r in self.retries if r[0] == agent_name]


# ---------------------------------------------------------------------------
# Faults
# ---------------------------------------------------------------------------


def resource_exhausted(retry_delay: str | None = None) -> Exception:
    """A RESOURCE_EXHAUSTED shaped like the one Vertex really raises.

    A Vertex 429 arrives as google.adk.models.google_llm
    ._ResourceExhaustedError -- a private ClientError subclass carrying
    .code == 429, .status == 'RESOURCE_EXHAUSTED' and the error JSON on
    .details. classify() matches it on both the numeric code and the
    string markers, and _PolicyWait reads .details for a
    google.rpc.RetryInfo. This reproduces all three surfaces without
    depending on a private ADK class.
    """
    details: dict[str, Any] = {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "message": "Quota exceeded for aiplatform.googleapis.com",
            "details": [],
        }
    }
    if retry_delay is not None:
        details["error"]["details"].append(
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": retry_delay,
            }
        )

    exc = Exception(f"429 RESOURCE_EXHAUSTED. {details}")
    exc.code = 429  # type: ignore[attr-defined]
    exc.status = "RESOURCE_EXHAUSTED"  # type: ignore[attr-defined]
    exc.details = details  # type: ignore[attr-defined]
    exc.response = None  # type: ignore[attr-defined]
    return exc


FAULTS: dict[str, Any] = {
    "rate_limit": resource_exhausted,
    "transient": lambda: Exception("Connection reset by peer"),
    "timeout": lambda: TimeoutError("deadline exceeded"),
    "invalid_output": lambda: Exception("missing_output for agent"),
    "safety": lambda: Exception("blocked_reason SAFETY"),
    "fatal": lambda: Exception("something nobody has ever seen before"),
}


# ---------------------------------------------------------------------------
# Typed success payloads, per step
# ---------------------------------------------------------------------------

_PLAN_JSON = (
    '{"domain_query_groups": [{"domain": "firmographics", "queries": '
    '["Acme Corp revenue", "Acme Corp employees", "Acme Corp HQ"]}]}'
)
_ALIGNMENT_JSON = (
    '{"alignment_mappings": [{"challenge_or_priority": "legacy WAN", '
    '"colt_solution": "SD-WAN", "alignment_justification": "modernize"}], '
    '"strategic_opportunity": {"summary": "Why Colt, why now", '
    '"hooks": ["hook"], "executive_narratives": [], '
    '"regulatory_triggers": [], "ai_urgency": [], '
    '"competitive_displacement_angles": [], "colt_differentiation": []}}'
)
_REPORT_MARKDOWN = "# Strategic Account Brief\n\nCompiled."


def _findings() -> SearchFindings:
    return SearchFindings(
        company="Acme Corp",
        domains={
            "firmographicsagent_output": DomainFinding(
                domain="firmographics",
                content="Acme Corp reported revenue of $2B.",
                evidence=(
                    Evidence(
                        url="https://example.com/acme",
                        title="Acme",
                        snippet="revenue $2B",
                        query="Acme Corp revenue",
                    ),
                ),
            )
        },
        executed=1,
    )


def _alignment() -> ColtAlignment:
    return ColtAlignment(
        mappings=(ColtAlignmentMapping("legacy WAN", "SD-WAN", "modernize"),),
        opportunity_summary="Why Colt, why now",
        hooks=("hook",),
    )


STEP_SPECS: dict[str, dict[str, Any]] = {
    "QueryPlanner": {
        "attr": "_planner",
        "request": lambda: ResearchRequest(company="Acme Corp", job_id="retry-test"),
        "success": lambda: QueryPlan(
            company="Acme Corp",
            queries=(Query(text="Acme Corp revenue", domain="firmographics"),),
        ),
        "llm_payload": _PLAN_JSON,
        "adk": True,
    },
    "SearchExecutor": {
        "attr": "_searcher",
        "request": lambda: QueryPlan(
            company="Acme Corp",
            queries=(Query(text="Acme Corp revenue", domain="firmographics"),),
        ),
        "success": _findings,
        "llm_payload": None,
        "adk": False,
    },
    "AlignmentAnalyst": {
        "attr": "_analyst",
        "request": _findings,
        "success": _alignment,
        "llm_payload": _ALIGNMENT_JSON,
        "adk": True,
    },
    "ReportCompiler": {
        "attr": "_compiler",
        "request": lambda: CompilerInput(
            company="Acme Corp", findings=_findings(), alignment=_alignment()
        ),
        "success": None,  # execute() is the real one in adk mode
        "llm_payload": _REPORT_MARKDOWN,
        "adk": True,
    },
}


class ScriptedLlm(BaseLlm):
    """A BaseLlm that fails a scripted number of times, then answers.

    Used by `--mode adk`, which is the only mode that also exercises
    AdkAgentStep.execute() -- a brand new InMemorySessionService and
    single-agent Runner per attempt. That fresh-session-per-attempt claim
    is the whole basis of the retry design, and patching `execute` cannot
    test it.
    """

    model: str = "fake-retry-test"
    payload: str = ""
    fail_times: int = 0
    fault: str = "rate_limit"
    retry_delay_hint: str | None = None

    def model_post_init(self, __context: Any) -> None:
        object.__setattr__(self, "_calls", 0)

    @property
    def calls(self) -> int:
        return getattr(self, "_calls", 0)

    async def generate_content_async(self, llm_request, stream: bool = False):
        object.__setattr__(self, "_calls", self.calls + 1)
        if self.calls <= self.fail_times:
            if self.fault == "rate_limit":
                raise resource_exhausted(self.retry_delay_hint)
            raise FAULTS[self.fault]()
        yield LlmResponse(
            content=genai_types.Content(
                role="model", parts=[genai_types.Part(text=self.payload)]
            ),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=30, candidates_token_count=120
            ),
        )


# ---------------------------------------------------------------------------
# Policies under test
#
# jitter=0 makes the backoff sequence exact and assertable; every other
# number is the real production value from src/shared/config.py, so the
# delays printed below are the schedule that actually runs in Cloud Run.
# Because the sleep is recorded rather than taken, asserting a 345s
# schedule costs milliseconds.
# ---------------------------------------------------------------------------


def fast_policy(**overrides: Any) -> RetryPolicy:
    base: dict[str, Any] = {
        "max_attempts": 3,
        "initial_delay": 2.0,
        "max_delay": 60.0,
        "exp_base": 2.0,
        "jitter": 0.0,
        "timeout": 30.0,
        "rate_limit_max_attempts": 6,
        "rate_limit_initial_delay": 15.0,
        "rate_limit_max_delay": 120.0,
    }
    base.update(overrides)
    return RetryPolicy(**base)


SCENARIOS: list[dict[str, Any]] = [
    {
        "name": "rate-limit recovers on 4th attempt",
        "fault": "rate_limit",
        "fail_times": 3,
        "attempts": 4,
        "delays": [15.0, 30.0, 60.0],
        "kind": ErrorKind.RATE_LIMIT,
        "outcome": "ok",
    },
    {
        "name": "rate-limit exhausts its own budget",
        "fault": "rate_limit",
        "fail_times": 99,
        "attempts": 6,
        "delays": [15.0, 30.0, 60.0, 120.0, 120.0],
        "kind": ErrorKind.RATE_LIMIT,
        "outcome": "AgentError",
    },
    {
        "name": "transient keeps the ordinary budget",
        "fault": "transient",
        "fail_times": 99,
        "attempts": 3,
        "delays": [2.0, 4.0],
        "kind": ErrorKind.TRANSIENT,
        "outcome": "AgentError",
    },
    {
        "name": "invalid output retries the step",
        "fault": "invalid_output",
        "fail_times": 1,
        "attempts": 2,
        "delays": [2.0],
        "kind": ErrorKind.INVALID_OUTPUT,
        "outcome": "ok",
    },
    {
        "name": "timeout retries the step",
        "fault": "timeout",
        "fail_times": 1,
        "attempts": 2,
        "delays": [2.0],
        "kind": ErrorKind.TIMEOUT,
        "outcome": "ok",
    },
    {
        "name": "safety never retries",
        "fault": "safety",
        "fail_times": 99,
        "attempts": 1,
        "delays": [],
        "kind": None,
        "outcome": "AgentError",
    },
    {
        "name": "fatal never retries",
        "fault": "fatal",
        "fail_times": 99,
        "attempts": 1,
        "delays": [],
        "kind": None,
        "outcome": "AgentError",
    },
    {
        "name": "server Retry-After overrides a shorter backoff",
        "fault": "rate_limit",
        "fail_times": 2,
        "retry_delay_hint": "45s",
        "attempts": 3,
        "delays": [45.0, 45.0],
        "kind": ErrorKind.RATE_LIMIT,
        "outcome": "ok",
    },
    {
        "name": "server Retry-After never shortens our backoff",
        "fault": "rate_limit",
        "fail_times": 3,
        "retry_delay_hint": "3s",
        "attempts": 4,
        "delays": [15.0, 30.0, 60.0],
        "kind": ErrorKind.RATE_LIMIT,
        "outcome": "ok",
    },
    {
        "name": "absurd Retry-After is ignored",
        "fault": "rate_limit",
        "fail_times": 1,
        "retry_delay_hint": "99999s",
        "attempts": 2,
        "delays": [15.0],
        "kind": ErrorKind.RATE_LIMIT,
        "outcome": "ok",
    },
]


class Results:
    def __init__(self, verbose: bool) -> None:
        self.rows: list[tuple[str, str, str, str]] = []
        self.failures = 0
        self.verbose = verbose

    def record(self, group: str, name: str, problems: list[str], detail: str) -> None:
        if problems:
            self.failures += 1
            self.rows.append((group, name, "FAIL", "; ".join(problems)))
        else:
            self.rows.append((group, name, "PASS", detail))

    def report(self) -> int:
        width = max((len(r[1]) for r in self.rows), default=10)
        group = None
        for g, name, status, detail in self.rows:
            if g != group:
                print("")
                print(g)
                print("-" * len(g))
                group = g
            line = "  [" + status + "] " + name.ljust(width)
            if status == "FAIL" or self.verbose:
                line += "  " + detail
            print(line)
        total = len(self.rows)
        passed = total - self.failures
        suffix = "" if not self.failures else "  (" + str(self.failures) + " FAILED)"
        print("")
        print(str(passed) + "/" + str(total) + " checks passed" + suffix)
        return 1 if self.failures else 0


def _check(
    scenario: dict[str, Any],
    obs: RecordingObserver,
    agent_name: str,
    outcome: str,
    error: AgentError | None,
    sleeps: list[float],
) -> tuple[list[str], str]:
    problems: list[str] = []
    attempts = obs.attempts_for(agent_name)
    delays = obs.delays_for(agent_name)
    kinds = obs.kinds_for(agent_name)

    if attempts != scenario["attempts"]:
        problems.append(
            "attempts " + str(attempts) + " != " + str(scenario["attempts"])
        )
    if delays != scenario["delays"]:
        problems.append("delays " + str(delays) + " != " + str(scenario["delays"]))
    if outcome != scenario["outcome"]:
        problems.append("outcome " + outcome + " != " + scenario["outcome"])
    if scenario["kind"] is not None and any(k != scenario["kind"] for k in kinds):
        problems.append("retry kinds " + str([str(k) for k in kinds]) + " impure")
    if error is not None:
        if error.attempts != scenario["attempts"]:
            problems.append("AgentError.attempts " + str(error.attempts))
        if scenario["kind"] is not None and error.kind != scenario["kind"]:
            problems.append("AgentError.kind " + str(error.kind))
    # The delay handed to the Observer must be the delay actually slept --
    # an observable retry timeline is only useful if it is the real one.
    if [round(s, 6) for s in sleeps] != delays:
        problems.append("slept " + str(sleeps) + " != observed " + str(delays))

    detail = "attempts=" + str(attempts) + " delays=" + str(delays) + " -> " + outcome
    return problems, detail


async def _run_one(agent: Any, request: Any, obs: RecordingObserver):
    try:
        await agent.run(request, obs)
    except AgentError as exc:
        return "AgentError", exc
    return "ok", None


def build_pipeline():
    """A production pipeline with no credentials and no network.

    get_genai_client() passes project and location explicitly, and
    google-genai only resolves ADC when `project` is empty, so the client
    is constructed lazily and never called. Redis is kept out by
    replacing the cache repository before the factory runs -- the same
    module-attribute seam local_core_loop.py uses.
    """
    import src.worker.dependencies as worker_deps

    class _NullCache:
        async def async_get_search(self, company, query):
            return None

        async def async_set_search(self, company, query, payload, domain=None):
            return None

    worker_deps.RedisSearchCacheRepository = _NullCache
    return worker_deps.build_research_pipeline()


# ---------------------------------------------------------------------------
# Mode: loop -- per-instance `execute` shadow
#
# Agent.run() calls `self.execute(request)`, so an instance attribute wins
# over the bound method. This drives the REAL retry loop (classify ->
# should_retry -> _PolicyWait -> on_retry -> sleep) for all four steps
# uniformly, including SearchExecutor, which subclasses Agent rather than
# AdkAgentStep. It deliberately skips each step's own execute() body; that
# is what --mode adk is for.
# ---------------------------------------------------------------------------


async def run_loop_scenarios(results: Results) -> None:
    from src.worker.agents.models import Report

    loop_success = {
        "QueryPlanner": STEP_SPECS["QueryPlanner"]["success"],
        "SearchExecutor": STEP_SPECS["SearchExecutor"]["success"],
        "AlignmentAnalyst": STEP_SPECS["AlignmentAnalyst"]["success"],
        "ReportCompiler": lambda: Report(
            markdown=_REPORT_MARKDOWN, validation_status="PASSED"
        ),
    }

    for agent_name, spec in STEP_SPECS.items():
        for scenario in SCENARIOS:
            pipeline = build_pipeline()
            step = getattr(pipeline, spec["attr"])
            step.retry = fast_policy()

            calls = {"n": 0}
            hint = scenario.get("retry_delay_hint")
            success = loop_success[agent_name]

            async def execute(request, _c=calls, _s=scenario, _h=hint, _ok=success):
                _c["n"] += 1
                if _c["n"] <= _s["fail_times"]:
                    if _s["fault"] == "rate_limit":
                        raise resource_exhausted(_h)
                    raise FAULTS[_s["fault"]]()
                return _ok()

            step.execute = execute
            obs = RecordingObserver()
            SLEEPS.clear()
            outcome, error = await _run_one(step, spec["request"](), obs)
            problems, detail = _check(
                scenario, obs, agent_name, outcome, error, list(SLEEPS)
            )
            results.record("loop: " + agent_name, scenario["name"], problems, detail)


# ---------------------------------------------------------------------------
# Mode: adk -- fake BaseLlm behind the real ADK Runner
#
# The only mode that also exercises AdkAgentStep.execute(): a brand new
# InMemorySessionService and single-agent Runner per attempt, the
# output_key emptiness check, to_output() and validate(). The design's
# central claim is that "a failed attempt simply discards its session and
# the next attempt starts fresh" -- shadowing execute() cannot test that,
# which is why both modes exist.
# ---------------------------------------------------------------------------

# An LLM that raises cannot also return a structurally-invalid payload;
# INVALID_OUTPUT is covered by the per-agent unit tests instead.
ADK_SCENARIOS = [s for s in SCENARIOS if s["fault"] != "invalid_output"]


async def run_adk_scenarios(results: Results) -> None:
    for agent_name, spec in STEP_SPECS.items():
        if not spec["adk"]:
            continue
        for scenario in ADK_SCENARIOS:
            pipeline = build_pipeline()
            step = getattr(pipeline, spec["attr"])
            step.retry = fast_policy()

            llm = ScriptedLlm(
                payload=spec["llm_payload"],
                fail_times=scenario["fail_times"],
                fault=scenario["fault"],
                retry_delay_hint=scenario.get("retry_delay_hint"),
            )
            original_build_agent = step.build_agent

            def build_agent(_orig=original_build_agent, _llm=llm):
                agent = _orig()
                agent.model = _llm
                return agent

            step.build_agent = build_agent

            if agent_name == "ReportCompiler":
                # Isolate the retry loop from the report's content gates;
                # those have their own tests under tests/worker/agents.
                async def _valid(_markdown):
                    return type("R", (), {"is_valid": True, "violations": []})()

                step._guardrail.validate = _valid

            obs = RecordingObserver()
            SLEEPS.clear()
            outcome, error = await _run_one(step, spec["request"](), obs)
            problems, detail = _check(
                scenario, obs, agent_name, outcome, error, list(SLEEPS)
            )
            # Exactly one LLM call per attempt proves a fresh Runner per
            # attempt rather than a resumed invocation.
            if llm.calls != scenario["attempts"]:
                problems.append(
                    "llm calls "
                    + str(llm.calls)
                    + " != attempts "
                    + str(scenario["attempts"])
                )
            results.record(
                "adk: " + agent_name,
                scenario["name"],
                problems,
                detail + " llm_calls=" + str(llm.calls),
            )


# ---------------------------------------------------------------------------
# SearchExecutor's per-query loop
#
# A second, independent retry loop underneath the step, and the layer that
# actually meets the quota wall (the step fans out ~30 grounded searches).
# It is invisible to the Observer -- Agent.run only sees the step -- so it
# is asserted on the recorded sleeps and the fake's call count.
# ---------------------------------------------------------------------------


async def run_per_query_scenarios(results: Results) -> None:
    cases = [
        ("per-query rate-limit recovers", "rate_limit", 2, [15.0, 30.0], 3, 1, 0),
        (
            "per-query rate-limit exhausts",
            "rate_limit",
            99,
            [15.0, 30.0, 60.0, 120.0, 120.0],
            6,
            0,
            1,
        ),
        ("per-query fatal fails fast", "fatal", 99, [], 1, 0, 1),
    ]
    for name, fault, fail_times, expected_delays, expected_calls, ok, failed in cases:
        pipeline = build_pipeline()
        searcher = pipeline._searcher
        searcher._query_retry = fast_policy()
        # Keep the step-level loop out of the way; this is a per-query test.
        searcher.retry = fast_policy(max_attempts=1, rate_limit_max_attempts=1)
        searcher._min_success_rate = 0.0

        calls = {"n": 0}

        async def _search_once(company, query, _c=calls, _ft=fail_times, _f=fault):
            _c["n"] += 1
            if _c["n"] <= _ft:
                if _f == "rate_limit":
                    raise resource_exhausted()
                raise FAULTS[_f]()
            return "Acme Corp revenue is $2B.", ()

        searcher._search_once = _search_once
        obs = RecordingObserver()
        SLEEPS.clear()
        plan = QueryPlan(
            company="Acme Corp",
            queries=(Query(text="Acme Corp revenue", domain="firmographics"),),
        )
        findings = await searcher.run(plan, obs)

        problems: list[str] = []
        slept = [round(s, 6) for s in SLEEPS]
        if slept != expected_delays:
            problems.append("delays " + str(slept) + " != " + str(expected_delays))
        if calls["n"] != expected_calls:
            problems.append(
                "query calls " + str(calls["n"]) + " != " + str(expected_calls)
            )
        if findings.executed != ok:
            problems.append("executed " + str(findings.executed) + " != " + str(ok))
        if len(findings.failed) != failed:
            problems.append(
                "failed " + str(len(findings.failed)) + " != " + str(failed)
            )
        results.record(
            "per-query: SearchExecutor",
            name,
            problems,
            "calls=" + str(calls["n"]) + " delays=" + str(slept),
        )


# ---------------------------------------------------------------------------
# Mixed error kinds
#
# The subtlest behaviour in the whole change, and the one a single global
# attempt counter gets wrong: a 429 must not spend the attempts a step
# needs for its real work. Measured on a shared counter, 429/429 followed
# by a validation failure gave the ReportCompiler ZERO revision attempts,
# because the two 429s had already advanced the counter to max_attempts.
# ---------------------------------------------------------------------------


async def run_mixed_kind_checks(results: Results) -> None:
    cases = [
        {
            "name": "429s do not spend the revision budget",
            "messages": [
                "429 RESOURCE_EXHAUSTED",
                "429 RESOURCE_EXHAUSTED",
                "validation failed: Section 1 missing",
                "validation failed: Section 2 missing",
            ],
            "attempts": 5,
            "outcome": "ok",
            "kinds": [
                ErrorKind.RATE_LIMIT,
                ErrorKind.RATE_LIMIT,
                ErrorKind.INVALID_OUTPUT,
                ErrorKind.INVALID_OUTPUT,
            ],
            "delays": [15.0, 30.0, 2.0, 4.0],
        },
        {
            "name": "each kind exhausts its own budget",
            "messages": ["429 RESOURCE_EXHAUSTED"] * 2
            + ["validation failed: nope"] * 10,
            "attempts": 5,
            "outcome": "AgentError",
            "kinds": [
                ErrorKind.RATE_LIMIT,
                ErrorKind.RATE_LIMIT,
                ErrorKind.INVALID_OUTPUT,
                ErrorKind.INVALID_OUTPUT,
            ],
            "delays": [15.0, 30.0, 2.0, 4.0],
        },
        {
            "name": "rate-limit ladder restarts at its own initial delay",
            "messages": [
                "Connection reset by peer",
                "Connection reset by peer",
                "429 RESOURCE_EXHAUSTED",
            ],
            "attempts": 4,
            "outcome": "ok",
            "kinds": [
                ErrorKind.TRANSIENT,
                ErrorKind.TRANSIENT,
                ErrorKind.RATE_LIMIT,
            ],
            # The 429 is the 3rd overall attempt but the 1st of its kind,
            # so it waits 15s, not 15*2^2 = 60s.
            "delays": [2.0, 4.0, 15.0],
        },
    ]

    for case in cases:
        pipeline = build_pipeline()
        step = pipeline._compiler
        step.retry = fast_policy()
        messages = case["messages"]
        calls = {"n": 0}

        async def execute(request, _c=calls, _m=messages):
            _c["n"] += 1
            if _c["n"] <= len(_m):
                raise Exception(_m[_c["n"] - 1])
            from src.worker.agents.models import Report

            return Report(markdown=_REPORT_MARKDOWN, validation_status="PASSED")

        step.execute = execute
        obs = RecordingObserver()
        SLEEPS.clear()
        outcome, error = await _run_one(
            step, STEP_SPECS["ReportCompiler"]["request"](), obs
        )

        problems: list[str] = []
        attempts = obs.attempts_for("ReportCompiler")
        kinds = obs.kinds_for("ReportCompiler")
        delays = obs.delays_for("ReportCompiler")
        if attempts != case["attempts"]:
            problems.append(
                "attempts " + str(attempts) + " != " + str(case["attempts"])
            )
        if outcome != case["outcome"]:
            problems.append("outcome " + outcome + " != " + case["outcome"])
        if kinds != case["kinds"]:
            problems.append(
                "kinds "
                + str([str(k) for k in kinds])
                + " != "
                + str([str(k) for k in case["kinds"]])
            )
        if delays != case["delays"]:
            problems.append("delays " + str(delays) + " != " + str(case["delays"]))
        results.record(
            "mixed kinds: ReportCompiler",
            case["name"],
            problems,
            "attempts=" + str(attempts) + " delays=" + str(delays),
        )


# ---------------------------------------------------------------------------
# The wall-clock step budget, measured for real
#
# max_elapsed is a HARD ceiling: no new attempt starts past it, and a
# running attempt's timeout is clamped to what remains. This is the only
# check that needs a real clock, so it sleeps for real with tiny delays.
# ---------------------------------------------------------------------------


async def run_budget_check(results: Results) -> None:
    global _REAL_SLEEP
    was = _REAL_SLEEP
    _REAL_SLEEP = True
    try:
        pipeline = build_pipeline()
        step = pipeline._compiler
        # A 30s per-attempt timeout that must never apply, because the
        # step's entire budget is 0.4s.
        step.retry = fast_policy(
            rate_limit_max_attempts=99,
            rate_limit_initial_delay=0.05,
            rate_limit_max_delay=0.05,
            timeout=30.0,
            max_elapsed=0.4,
        )

        async def execute(request):
            await asyncio.sleep(10.0)
            return None

        step.execute = execute
        obs = RecordingObserver()
        started = time.monotonic()
        outcome, error = await _run_one(
            step, STEP_SPECS["ReportCompiler"]["request"](), obs
        )
        elapsed = time.monotonic() - started

        problems: list[str] = []
        if outcome != "AgentError":
            problems.append("outcome " + outcome + " != AgentError")
        if error is not None and error.kind != ErrorKind.TIMEOUT:
            problems.append("kind " + str(error.kind) + " != TIMEOUT")
        # Would be >= 10s if the 30s per-attempt timeout had applied, and
        # unbounded if max_elapsed only gated starting a new attempt.
        if elapsed > 3.0:
            problems.append(
                "elapsed " + format(elapsed, ".2f") + "s blew the 0.4s budget"
            )
        results.record(
            "budget: ReportCompiler",
            "max_elapsed hard-caps a slow attempt",
            problems,
            "elapsed=" + format(elapsed, ".2f") + "s",
        )
    finally:
        _REAL_SLEEP = was


# ---------------------------------------------------------------------------
# Production configuration audit
#
# Reads the policies build_research_pipeline() actually wires, so a config
# regression -- a step left without the rate-limit budget, or budgets that
# no longer fit the dispatch deadline -- fails here rather than in Cloud Run.
# ---------------------------------------------------------------------------


def audit_production_config(results: Results, verbose: bool) -> None:
    import src.worker.dependencies as worker_deps
    from src.shared.config import settings

    pipeline = worker_deps.build_research_pipeline()
    steps = {
        "QueryPlanner": pipeline._planner.retry,
        "SearchExecutor (step)": pipeline._searcher.retry,
        "SearchExecutor (query)": pipeline._searcher._query_retry,
        "AlignmentAnalyst": pipeline._analyst.retry,
        "ReportCompiler": pipeline._compiler.retry,
    }

    if verbose:
        print("")
        print("Wired production retry policies")
        print("-------------------------------")
        for name, policy in steps.items():
            print(
                "  "
                + name.ljust(24)
                + " attempts="
                + str(policy.max_attempts)
                + " rl_attempts="
                + str(policy.rate_limit_max_attempts)
                + " rl_initial="
                + str(policy.rate_limit_initial_delay)
                + " rl_max="
                + str(policy.rate_limit_max_delay)
                + " timeout="
                + str(policy.timeout)
                + " budget="
                + str(policy.max_elapsed)
            )

    for name, policy in steps.items():
        problems: list[str] = []
        if not policy.rate_limit_max_attempts:
            problems.append("no rate-limit attempt budget")
        elif policy.rate_limit_max_attempts <= policy.max_attempts:
            problems.append(
                "rate-limit budget "
                + str(policy.rate_limit_max_attempts)
                + " not larger than ordinary "
                + str(policy.max_attempts)
            )
        if not policy.respect_retry_after:
            problems.append("ignores server Retry-After")
        sleeps = [
            policy.delay_for(n, ErrorKind.RATE_LIMIT)
            for n in range(1, policy.max_attempts_for(ErrorKind.RATE_LIMIT))
        ]
        total = sum(sleeps)
        # Must comfortably outlast a 60s per-minute Vertex quota window.
        if total < 120.0:
            problems.append(
                "429 backoff totals only "
                + format(total, ".0f")
                + "s (< 2 quota windows)"
            )
        results.record(
            "config: policies",
            name,
            problems,
            "429 backoff ~"
            + format(total, ".0f")
            + "s over "
            + str(policy.max_attempts_for(ErrorKind.RATE_LIMIT))
            + " attempts",
        )

    budgets = {
        "planner": settings.PLANNER_STEP_BUDGET_SECONDS,
        "search": settings.SEARCH_STEP_BUDGET_SECONDS,
        "alignment": settings.ALIGNMENT_STEP_BUDGET_SECONDS,
        "compiler": settings.COMPILER_STEP_BUDGET_SECONDS,
    }
    total_budget = sum(budgets.values())
    deadline = float(settings.CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS)
    problems = []
    if total_budget >= deadline:
        problems.append(
            "step budgets total "
            + format(total_budget, ".0f")
            + "s >= deadline "
            + format(deadline, ".0f")
            + "s"
        )
    if deadline - total_budget < 200:
        problems.append(
            "only "
            + format(deadline - total_budget, ".0f")
            + "s left for finalization (want >= 200s)"
        )
    results.record(
        "config: wall clock",
        "step budgets fit the dispatch deadline",
        problems,
        " + ".join(k + "=" + format(v, ".0f") for k, v in budgets.items())
        + " = "
        + format(total_budget, ".0f")
        + "s of "
        + format(deadline, ".0f")
        + "s",
    )

    problems = []
    if 429 in settings.GEMINI_RETRY_STATUS_CODES:
        problems.append(
            "429 is in GEMINI_RETRY_STATUS_CODES: the in-client HTTP retry would "
            "multiply against the agent policy and can surface a quota error as a "
            "bare TIMEOUT"
        )
    results.record(
        "config: wall clock",
        "429 is owned by exactly one retry layer",
        problems,
        "GEMINI_RETRY_STATUS_CODES=" + str(settings.GEMINI_RETRY_STATUS_CODES),
    )

    # --- queue-level re-delivery ------------------------------------------
    # The outermost retry layer, and the only one whose backoff is
    # minutes-scale (10s-300s over up to an hour) rather than
    # seconds-scale -- which is what a Vertex quota outage actually needs.
    # It was structurally dead before: the runner wrote FAILED and the next
    # delivery read it back and no-opped, so 4 of the queue's 5 attempts
    # were wasted. These checks stop it being silently disabled again.
    max_deliveries = int(settings.CLOUD_TASKS_MAX_ATTEMPTS)
    problems = []
    if max_deliveries < 2:
        problems.append(
            "CLOUD_TASKS_MAX_ATTEMPTS="
            + str(max_deliveries)
            + " disables queue-level retry entirely"
        )
    if ErrorKind.RATE_LIMIT not in QUEUE_RETRYABLE_KINDS:
        problems.append("RATE_LIMIT is not queue-retryable")
    results.record(
        "config: cloud tasks",
        "queue-level re-delivery is enabled for 429",
        problems,
        "max_attempts="
        + str(max_deliveries)
        + " retryable="
        + str(sorted(str(k) for k in QUEUE_RETRYABLE_KINDS)),
    )

    # A rate-limited pipeline failure must be re-delivered while deliveries
    # remain, and must settle as FAILED on the last one.
    quota_error = Exception("429 RESOURCE_EXHAUSTED. Quota exceeded")
    first = TaskAttempt(retry_count=0, max_attempts=max_deliveries)
    last = TaskAttempt(retry_count=max_deliveries - 1, max_attempts=max_deliveries)
    problems = []
    if not should_redispatch(quota_error, first, JobPhase.PIPELINE):
        problems.append("a 429 on the first delivery is not re-delivered")
    if should_redispatch(quota_error, last, JobPhase.PIPELINE):
        problems.append("the final delivery would leave the job non-terminal")
    if should_redispatch(quota_error, first, JobPhase.FINALIZATION):
        problems.append(
            "a finalization failure would be re-delivered, duplicating side effects"
        )
    if should_redispatch(Exception("blocked_reason SAFETY"), first, JobPhase.PIPELINE):
        problems.append("a deterministic SAFETY block would be re-delivered")
    if should_redispatch(quota_error, None, JobPhase.PIPELINE):
        problems.append("a direct (non-queue) dispatch would be left non-terminal")
    results.record(
        "config: cloud tasks",
        "re-dispatch decision is correct at both ends",
        problems,
        "429 first=redeliver last=settle, finalization=settle, safety=settle",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline, deterministic test of the agents' tenacity retry loop."
    )
    parser.add_argument(
        "--mode",
        choices=["all", "loop", "adk", "per-query", "mixed", "budget", "config"],
        default="all",
        help=(
            "loop: per-instance execute shadow, all 4 steps. "
            "adk: fake BaseLlm behind the real ADK Runner, 3 steps. "
            "per-query: SearchExecutor's inner loop. "
            "mixed: interleaved error kinds and their separate budgets. "
            "budget: the wall-clock step ceiling. "
            "config: audit the wired production policies. Default: all."
        ),
    )
    parser.add_argument(
        "--real-sleep",
        action="store_true",
        help=(
            "Actually sleep the backoff instead of recording it. Turns a 345s "
            "production schedule into 345 real seconds -- for watching, not testing."
        ),
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show detail for passing checks."
    )
    return parser.parse_args(argv)


async def run_all(args: argparse.Namespace, results: Results) -> None:
    if args.mode in {"all", "loop"}:
        await run_loop_scenarios(results)
    if args.mode in {"all", "adk"}:
        await run_adk_scenarios(results)
    if args.mode in {"all", "per-query"}:
        await run_per_query_scenarios(results)
    if args.mode in {"all", "mixed"}:
        await run_mixed_kind_checks(results)
    if args.mode in {"all", "budget"}:
        await run_budget_check(results)
    if args.mode in {"all", "config"}:
        audit_production_config(results, args.verbose)


def main(argv: list[str] | None = None) -> int:
    global _REAL_SLEEP
    args = parse_args(argv)
    _REAL_SLEEP = args.real_sleep
    install_sleep_recorder()

    print("Retry-loop test: offline, no credentials, no network, no quota spent.")
    print(
        "Backoff is "
        + ("slept for real." if args.real_sleep else "recorded, not slept.")
        + " Delays shown are the real production schedule (jitter forced to 0)."
    )

    results = Results(verbose=args.verbose)
    asyncio.run(run_all(args, results))
    exit_code = results.report()
    print("")
    print("OK" if exit_code == 0 else "FAILED")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
