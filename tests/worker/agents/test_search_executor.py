"""Tests for SearchExecutor and RateLimiter (src/worker/agents/search.py).

Covers the three defects fixed here (see IMPLEMENTATION_PLAN.md):
  A2 - search now retries per-query with backoff instead of never retrying.
  A3 - RateLimiter enforces real QPS, not just a concurrency semaphore.
  R3/R4 - failed queries are recorded honestly, never billed as successes.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from src.worker.agents.base import AgentError, ErrorKind, RetryPolicy
from src.worker.agents.models import Query, QueryPlan
from src.worker.agents.search import (
    DOMAIN_SLUG_TO_OUTPUT_KEY,
    RateLimiter,
    SearchExecutor,
)
from src.worker.domain.contracts import DOMAIN_OUTPUT_KEYS
from src.worker.observers import Observer


class RecordingObserver(Observer):
    def __init__(self) -> None:
        self.retries = []
        self.successes = []
        self.failures = []

    def on_start(self, agent_name, attempt):
        pass

    def on_retry(self, agent_name, attempt, kind, delay):
        self.retries.append((agent_name, attempt, kind, delay))

    def on_success(self, agent_name, attempt, seconds):
        self.successes.append((agent_name, attempt, seconds))

    def on_failure(self, agent_name, attempt, kind, exc):
        self.failures.append((agent_name, attempt, kind, exc))


class FakeCache:
    """No-op cache: every query is a miss, writes are recorded."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, dict]] = []

    async def async_get_search(self, company, query):
        return None

    async def async_set_search(self, company, query, results, domain=None):
        self.writes.append((company, query, results))
        return True


def _make_response(text: str, sources: list[str] | None = None):
    grounding_chunks = [
        SimpleNamespace(web=SimpleNamespace(uri=src, title="Source"))
        for src in (sources or [])
    ]
    candidate = SimpleNamespace(
        grounding_metadata=SimpleNamespace(grounding_chunks=grounding_chunks)
    )
    return SimpleNamespace(text=text, candidates=[candidate])


class FakeModels:
    """Simulates client.aio.models.generate_content with scripted behavior."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self.call_count = 0

    async def generate_content(self, *, model, contents, config):
        self.call_count += 1
        return await self._responder(self.call_count, contents)


class FakeAsyncNamespace:
    def __init__(self, models: FakeModels) -> None:
        self.models = models


class FakeGenaiClient:
    def __init__(self, responder) -> None:
        self.aio = FakeAsyncNamespace(FakeModels(responder))


def _plan(*queries: tuple[str, str]) -> QueryPlan:
    return QueryPlan(
        company="Acme",
        queries=tuple(Query(text=t, domain=d) for t, d in queries),
    )


class TestDomainSlugMapping:
    def test_covers_every_canonical_output_key_exactly_once(self):
        assert set(DOMAIN_SLUG_TO_OUTPUT_KEY.values()) == set(DOMAIN_OUTPUT_KEYS)
        assert len(DOMAIN_SLUG_TO_OUTPUT_KEY) == len(DOMAIN_OUTPUT_KEYS)

    def test_tech_stack_slug_maps_correctly(self):
        """Regression test for bug C3: tech_stack (BM25 domain slug) must
        map to techstackagent_output, not silently fail to match.
        """
        assert DOMAIN_SLUG_TO_OUTPUT_KEY["tech_stack"] == "techstackagent_output"


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_respects_qps_ceiling(self):
        limiter = RateLimiter(qps=10.0, burst=1)
        await limiter.acquire()  # consumes the initial token instantly
        start = time.monotonic()
        await limiter.acquire()  # must wait ~1/10s for the next token
        elapsed = time.monotonic() - start
        assert elapsed >= 0.08  # allow scheduling slack

    @pytest.mark.asyncio
    async def test_burst_allows_immediate_acquisitions_up_to_capacity(self):
        limiter = RateLimiter(qps=1.0, burst=5)
        start = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.05

    def test_penalize_halves_effective_rate(self):
        limiter = RateLimiter(qps=10.0, burst=5)
        limiter.penalize()
        assert limiter._effective_qps == pytest.approx(5.0)

    def test_recover_restores_base_rate_after_cooldown(self):
        limiter = RateLimiter(qps=10.0, burst=5, penalty_cooldown=0.0)
        limiter.penalize()
        limiter.recover()
        assert limiter._effective_qps == pytest.approx(10.0)

    def test_recover_no_op_before_cooldown_elapses(self):
        limiter = RateLimiter(qps=10.0, burst=5, penalty_cooldown=100.0)
        limiter.penalize()
        limiter.recover()
        assert limiter._effective_qps == pytest.approx(5.0)


def _make_executor(responder, *, min_success_rate: float = 0.6, query_retry=None):
    client = FakeGenaiClient(responder)
    cache = FakeCache()
    executor = SearchExecutor(
        client,
        cache,
        model="fake-search-model",
        qps=1000.0,  # effectively unthrottled for fast tests
        qps_burst=1000,
        concurrency=8,
        query_retry=query_retry
        or RetryPolicy(max_attempts=2, initial_delay=0.001, jitter=0.0),
        min_success_rate=min_success_rate,
        step_retry=RetryPolicy(max_attempts=2, initial_delay=0.001, jitter=0.0),
    )
    return executor, client, cache


@pytest.mark.asyncio
async def test_search_executor_all_succeed_populates_domains_and_evidence():
    async def responder(call_count, contents):
        return _make_response("Acme facts", sources=["https://reuters.com/x"])

    executor, _, cache = _make_executor(responder)
    plan = _plan(("Acme revenue", "firmographics"), ("Acme cloud stack", "tech_stack"))

    findings = await executor.run(plan, RecordingObserver())

    assert findings.executed == 2
    assert findings.failed == ()
    assert findings.success_rate == 1.0
    assert findings.domains["firmographicsagent_output"].content == "Acme facts"
    assert findings.domains["techstackagent_output"].content == "Acme facts"
    assert len(findings.domains["firmographicsagent_output"].evidence) == 1
    assert len(cache.writes) == 2  # both fresh results cached


@pytest.mark.asyncio
async def test_search_executor_retries_transient_failure_then_succeeds():
    async def responder(call_count, contents):
        if call_count == 1:
            raise Exception("429 rate limit")  # noqa: TRY002
        return _make_response("recovered text")

    executor, client, _ = _make_executor(responder)
    plan = _plan(("only query", "firmographics"))

    findings = await executor.run(plan, RecordingObserver())

    assert findings.executed == 1
    assert findings.failed == ()
    assert client.aio.models.call_count == 2


@pytest.mark.asyncio
async def test_search_executor_records_honest_failure_not_fake_text():
    """Regression test for R3: a permanently failing query must show up as
    a failure, never as fabricated placeholder text counted as content.
    """

    async def responder(call_count, contents):
        raise Exception("totally broken")  # noqa: TRY002

    executor, _, cache = _make_executor(responder, min_success_rate=0.0)
    plan = _plan(("bad query", "firmographics"))

    findings = await executor.run(plan, RecordingObserver())

    assert findings.executed == 0
    assert findings.failed == ("bad query",)
    assert findings.domains["firmographicsagent_output"].content == ""
    assert cache.writes == []  # never cache a failure


@pytest.mark.asyncio
async def test_search_executor_validate_rejects_low_success_rate():
    """Regression test for A2: SearchExecutor must be retryable as a whole
    step when too many individual queries fail.
    """
    call_state = {"n": 0}

    async def responder(call_count, contents):
        call_state["n"] += 1
        # First full pass: everything fails. Second pass: everything succeeds.
        if call_state["n"] <= 3:
            raise Exception("resource_exhausted")  # noqa: TRY002
        return _make_response("ok")

    executor, _, _ = _make_executor(
        responder,
        min_success_rate=0.99,
        query_retry=RetryPolicy(max_attempts=1, initial_delay=0.0),
    )
    plan = _plan(("q1", "firmographics"), ("q2", "market"), ("q3", "strategy"))

    obs = RecordingObserver()
    findings = await executor.run(plan, obs)

    # First attempt: 3 queries all fail (min_success_rate=0.99 rejects it).
    # Second attempt (step-level retry): all 3 succeed.
    assert findings.executed == 3
    assert len(obs.retries) == 1
    assert obs.retries[0][2].name == "INVALID_OUTPUT"


@pytest.mark.asyncio
async def test_search_executor_cache_hits_are_not_counted_as_fresh_searches():
    """Regression test for R4: cache hits must not be double-billed as
    fresh searches when computing executed count for cost attribution.
    """

    class HitCache(FakeCache):
        async def async_get_search(self, company, query):
            return {"results": {"text": "cached text", "sources": []}}

    async def responder(call_count, contents):
        raise AssertionError("should never call the model for a cache hit")

    client = FakeGenaiClient(responder)
    cache = HitCache()
    executor = SearchExecutor(
        client,
        cache,
        model="fake-model",
        qps=1000.0,
        qps_burst=1000,
        concurrency=8,
    )
    plan = _plan(("cached query", "firmographics"))

    findings = await executor.run(plan, RecordingObserver())

    # Cache hits still count toward "executed" (they are real prior
    # successes), but no new model call and no new cache write occurred.
    assert findings.executed == 1
    assert cache.writes == []


# ---------------------------------------------------------------------------
# Regression tests for the 2026-09-02 prod TIMEOUT (job: "Societe Generale")
#
# A grounded-search call wedged inside _search_once. _run_one awaited it
# bare -- the configured per-query timeout (SEARCH_TIMEOUT_SECONDS, passed
# in as query_retry.timeout) was never applied -- so the hung query held a
# concurrency-semaphore slot until Agent.run()'s step-level asyncio.wait_for
# killed the whole step at 300s. That repeated for all 3 step attempts, and
# because successful results were only cached in a post-gather loop, each
# cancellation discarded every completed search and the retries started from
# cold. The step failed with kind=TIMEOUT and an empty error string
# (str(asyncio.TimeoutError()) == ""), taking the job with it.
# ---------------------------------------------------------------------------


async def _hang_forever() -> None:
    await asyncio.Event().wait()  # never set


@pytest.mark.asyncio
async def test_hung_query_is_bounded_by_query_timeout_not_the_step_deadline():
    """One wedged query must cost only itself, never the whole step."""

    async def responder(call_count, contents):
        if "HANGS" in contents:
            await _hang_forever()
        return _make_response("good text", sources=["https://reuters.com/x"])

    executor, _, _ = _make_executor(
        responder,
        min_success_rate=0.5,
        query_retry=RetryPolicy(
            max_attempts=2, initial_delay=0.001, jitter=0.0, timeout=0.05
        ),
    )
    # A generous step budget: if the per-query timeout were missing this
    # test would hang until the step deadline instead of returning.
    executor.retry = RetryPolicy(max_attempts=1, timeout=30.0)
    plan = _plan(("HANGS forever", "firmographics"), ("fine query", "tech_stack"))

    findings = await executor.run(plan, RecordingObserver())

    assert findings.executed == 1
    assert findings.failed == ("HANGS forever",)
    assert findings.domains["techstackagent_output"].content == "good text"
    assert findings.domains["firmographicsagent_output"].content == ""


@pytest.mark.asyncio
async def test_query_timeout_is_classified_as_timeout_and_retried_per_query():
    """The bounded await must produce a retryable TIMEOUT, consuming the
    query's own retry budget rather than surfacing as a step failure.
    """
    calls: list[str] = []

    async def responder(call_count, contents):
        calls.append(contents)
        await _hang_forever()

    executor, client, cache = _make_executor(
        responder,
        min_success_rate=0.0,  # accept an all-failed result so run() returns
        query_retry=RetryPolicy(
            max_attempts=3, initial_delay=0.001, jitter=0.0, timeout=0.05
        ),
    )
    executor.retry = RetryPolicy(max_attempts=1, timeout=30.0)
    plan = _plan(("HANGS forever", "firmographics"))

    findings = await executor.run(plan, RecordingObserver())

    assert findings.executed == 0
    assert findings.failed == ("HANGS forever",)
    # Every one of the query's own attempts ran and timed out.
    assert client.aio.models.call_count == 3
    assert cache.writes == []


@pytest.mark.asyncio
async def test_successful_results_are_cached_before_a_step_timeout_cancels():
    """A step-level timeout must not discard searches that already finished.

    Before the fix the cache write happened in a loop after
    asyncio.gather() returned, so cancelling the gather threw away every
    completed result and the next step attempt re-ran the whole plan.
    """

    async def responder(call_count, contents):
        if "HANGS" in contents:
            await _hang_forever()
        return _make_response("fast result", sources=["https://reuters.com/x"])

    executor, _, cache = _make_executor(
        responder,
        # No per-query bound here: this test is specifically about what
        # survives a step-level cancellation.
        query_retry=RetryPolicy(max_attempts=1, initial_delay=0.001, jitter=0.0),
    )
    executor.retry = RetryPolicy(max_attempts=1, timeout=0.3)
    plan = _plan(("HANGS forever", "firmographics"), ("fast query", "tech_stack"))

    with pytest.raises(AgentError) as excinfo:
        await executor.run(plan, RecordingObserver())

    assert excinfo.value.kind is ErrorKind.TIMEOUT
    # The completed query is already durable despite the cancellation.
    assert [q for _c, q, _r in cache.writes] == ["fast query"]
