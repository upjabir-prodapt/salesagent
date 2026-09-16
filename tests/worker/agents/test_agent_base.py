"""Unit tests for the Agent template method in src/worker/agents/base.py.

These tests verify the core requirement: when a step's execute() fails,
only that step retries (in-place, via its own RetryPolicy), with no
shared state or cross-step effects.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import pytest

from src.worker.agents.base import (
    Agent,
    AgentError,
    ErrorKind,
    InvalidOutputError,
    RetryPolicy,
    _format_result_for_log,
)
from src.worker.observers import Observer


class RecordingObserver(Observer):
    """Captures every hook call for assertions."""

    def __init__(self) -> None:
        self.starts: list[tuple[str, int]] = []
        self.retries: list[tuple[str, int, ErrorKind, float]] = []
        self.successes: list[tuple[str, int, float]] = []
        self.failures: list[tuple[str, int, ErrorKind, BaseException]] = []

    def on_start(self, agent_name, attempt):
        self.starts.append((agent_name, attempt))

    def on_retry(self, agent_name, attempt, kind, delay):
        self.retries.append((agent_name, attempt, kind, delay))

    def on_success(self, agent_name, attempt, seconds):
        self.successes.append((agent_name, attempt, seconds))

    def on_failure(self, agent_name, attempt, kind, exc):
        self.failures.append((agent_name, attempt, kind, exc))


class FlakyAgent(Agent[str, str]):
    """Fails N times with a retryable error, then succeeds."""

    name = "FlakyAgent"
    retry = RetryPolicy(max_attempts=3, initial_delay=0.001, jitter=0.0)

    def __init__(self, fail_times: int) -> None:
        self._fail_times = fail_times
        self._calls = 0

    async def execute(self, request: str) -> str:
        self._calls += 1
        if self._calls <= self._fail_times:
            raise Exception("429 rate limit")  # noqa: TRY002 - deliberate test error
        return f"ok:{request}:{self._calls}"


class AlwaysFatalAgent(Agent[str, str]):
    name = "AlwaysFatalAgent"
    retry = RetryPolicy(max_attempts=3, initial_delay=0.001, jitter=0.0)

    async def execute(self, request: str) -> str:
        raise ValueError("totally unrelated fatal error")


class ValidatingAgent(Agent[str, str]):
    """Succeeds structurally but validate() rejects until 2nd attempt."""

    name = "ValidatingAgent"
    retry = RetryPolicy(max_attempts=3, initial_delay=0.001, jitter=0.0)

    def __init__(self) -> None:
        self._calls = 0

    async def execute(self, request: str) -> str:
        self._calls += 1
        return f"attempt-{self._calls}"

    def validate(self, result: str) -> None:
        if result == "attempt-1":
            raise InvalidOutputError(
                "first attempt output rejected", agent_name=self.name
            )


class SlowAgent(Agent[str, str]):
    name = "SlowAgent"
    retry = RetryPolicy(max_attempts=1, timeout=0.05)

    async def execute(self, request: str) -> str:
        await asyncio.sleep(1.0)
        return "never"


@pytest.mark.asyncio
async def test_flaky_agent_recovers_within_budget():
    obs = RecordingObserver()
    agent = FlakyAgent(fail_times=2)
    result = await agent.run("company", obs)
    assert result == "ok:company:3"
    assert len(obs.retries) == 2
    assert len(obs.successes) == 1
    assert obs.successes[0][1] == 3  # succeeded on 3rd attempt


@pytest.mark.asyncio
async def test_agent_exhausts_retry_budget_and_raises_agent_error():
    obs = RecordingObserver()
    agent = FlakyAgent(fail_times=10)  # never succeeds within max_attempts=3
    with pytest.raises(AgentError) as exc_info:
        await agent.run("company", obs)
    assert exc_info.value.agent_name == "FlakyAgent"
    assert exc_info.value.attempts == 3
    assert len(obs.failures) == 1
    assert (
        len(obs.retries) == 2
    )  # retried after attempt 1 and 2, failed permanently at 3


@pytest.mark.asyncio
async def test_fatal_error_never_retries():
    obs = RecordingObserver()
    agent = AlwaysFatalAgent()
    with pytest.raises(AgentError) as exc_info:
        await agent.run("company", obs)
    assert exc_info.value.kind == ErrorKind.FATAL
    assert exc_info.value.attempts == 1
    assert len(obs.retries) == 0
    assert len(obs.failures) == 1


@pytest.mark.asyncio
async def test_validate_hook_triggers_retry_of_same_step():
    obs = RecordingObserver()
    agent = ValidatingAgent()
    result = await agent.run("company", obs)
    assert result == "attempt-2"
    assert len(obs.retries) == 1
    assert obs.retries[0][2] == ErrorKind.INVALID_OUTPUT


@pytest.mark.asyncio
async def test_timeout_is_enforced_and_classified():
    obs = RecordingObserver()
    agent = SlowAgent()
    with pytest.raises(AgentError) as exc_info:
        await agent.run("company", obs)
    assert exc_info.value.kind == ErrorKind.TIMEOUT


@pytest.mark.asyncio
async def test_two_independent_agents_do_not_share_retry_state():
    """Regression test for bug A1: leaf and runner retry layers used to
    share one counter per agent name. Two separate Agent instances (as
    the pipeline uses -- one per step) must never affect each other.
    """
    obs = RecordingObserver()
    agent_a = FlakyAgent(fail_times=2)
    agent_b = FlakyAgent(fail_times=0)

    result_b = await agent_b.run("b", obs)
    assert result_b == "ok:b:1"

    # agent_a's budget must be untouched by agent_b's successful run
    result_a = await agent_a.run("a", obs)
    assert result_a == "ok:a:3"


@dataclass(frozen=True)
class _FakeReport:
    markdown: str
    validation_status: str = "PASSED"


def test_format_result_for_log_truncates_long_dataclass_fields():
    """Regression: logging an agent's full typed result (e.g. Report with
    a 40k+ char markdown body) must not dump the whole field verbatim.
    """
    huge_markdown = "x" * 10_000
    rendered = _format_result_for_log(_FakeReport(markdown=huge_markdown))
    assert "_FakeReport(" in rendered
    assert "validation_status='PASSED'" in rendered
    assert "[truncated" in rendered
    assert len(rendered) < len(huge_markdown)


def test_format_result_for_log_keeps_short_results_intact():
    rendered = _format_result_for_log(_FakeReport(markdown="short body"))
    assert rendered == "_FakeReport(markdown='short body', validation_status='PASSED')"


def test_format_result_for_log_falls_back_to_repr_for_non_dataclass():
    assert _format_result_for_log(["a", "b"]) == repr(["a", "b"])
    assert _format_result_for_log("plain string") == "'plain string'"


@pytest.mark.asyncio
async def test_agent_run_logs_agent_response_on_success(caplog):
    """Every agent's response must be written to the log (worker log file
    mirrors this via LOG_FILE) on successful completion, not just via the
    Observer callbacks.
    """
    obs = RecordingObserver()
    agent = FlakyAgent(fail_times=0)
    with caplog.at_level("INFO"):
        result = await agent.run("company", obs)
    assert result == "ok:company:1"
    assert any(
        "[AgentResponse] FlakyAgent succeeded" in record.message
        for record in caplog.records
    )


class KindedAgent(Agent[str, str]):
    """Always fails, with a message that classifies to a chosen kind."""

    name = "KindedAgent"

    def __init__(self, message: str, retry: RetryPolicy) -> None:
        self._message = message
        self.retry = retry
        self.calls = 0

    async def execute(self, request: str) -> str:
        self.calls += 1
        raise Exception(self._message)  # noqa: TRY002 - deliberate test error


_INSTANT_RATE_LIMIT = RetryPolicy(
    max_attempts=2,
    rate_limit_max_attempts=5,
    initial_delay=0.0,
    rate_limit_initial_delay=0.0,
    max_delay=0.0,
    rate_limit_max_delay=0.0,
    jitter=0.0,
)


@pytest.mark.asyncio
async def test_rate_limit_gets_the_larger_attempt_budget():
    """A RESOURCE_EXHAUSTED must not be spent on the same short budget as
    a malformed response. This is the behaviour the compiler failure was
    about: the old loop gave a 429 exactly 3 attempts and ~3s of backoff.
    """
    obs = RecordingObserver()
    agent = KindedAgent("429 RESOURCE_EXHAUSTED", _INSTANT_RATE_LIMIT)
    with pytest.raises(AgentError) as exc_info:
        await agent.run("company", obs)
    assert exc_info.value.kind == ErrorKind.RATE_LIMIT
    assert exc_info.value.attempts == 5
    assert agent.calls == 5
    assert len(obs.retries) == 4
    assert all(r[2] == ErrorKind.RATE_LIMIT for r in obs.retries)


@pytest.mark.asyncio
async def test_other_retryable_kinds_keep_the_ordinary_budget():
    """The larger budget is scoped to RATE_LIMIT alone -- an agent that
    keeps emitting invalid output must not get 5 expensive attempts."""
    obs = RecordingObserver()
    agent = KindedAgent("Connection reset by peer", _INSTANT_RATE_LIMIT)
    with pytest.raises(AgentError) as exc_info:
        await agent.run("company", obs)
    assert exc_info.value.kind == ErrorKind.TRANSIENT
    assert exc_info.value.attempts == 2
    assert agent.calls == 2


@pytest.mark.asyncio
async def test_rate_limit_backoff_is_reported_to_the_observer():
    """on_retry must carry the real delay, so the retry timeline is
    observable (the delay is what a test script asserts on)."""
    obs = RecordingObserver()
    agent = KindedAgent(
        "429 RESOURCE_EXHAUSTED",
        RetryPolicy(
            max_attempts=1,
            rate_limit_max_attempts=4,
            rate_limit_initial_delay=0.001,
            rate_limit_max_delay=1.0,
            jitter=0.0,
        ),
    )
    with pytest.raises(AgentError):
        await agent.run("company", obs)
    delays = [r[3] for r in obs.retries]
    assert delays == [0.001, 0.002, 0.004]


class BudgetBurningAgent(Agent[str, str]):
    """Sleeps longer than the step's whole wall-clock budget."""

    name = "BudgetBurningAgent"
    retry = RetryPolicy(
        max_attempts=5, initial_delay=0.0, jitter=0.0, timeout=30.0, max_elapsed=0.1
    )

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: str) -> str:
        self.calls += 1
        await asyncio.sleep(5.0)
        return "never"


@pytest.mark.asyncio
async def test_max_elapsed_clamps_the_per_attempt_timeout():
    """max_elapsed is a HARD ceiling, not just a gate on starting another
    attempt: the per-attempt timeout is clamped to what remains. Without
    the clamp a running attempt could overshoot its step budget by its
    full timeout, which is how four steps with generous timeouts added up
    to 2413s against an 1800s dispatch deadline.
    """
    obs = RecordingObserver()
    agent = BudgetBurningAgent()
    started = time.monotonic()
    with pytest.raises(AgentError) as exc_info:
        await agent.run("company", obs)
    elapsed = time.monotonic() - started
    assert exc_info.value.kind == ErrorKind.TIMEOUT
    # The 30s per-attempt timeout never applied; the 0.1s budget did.
    assert elapsed < 5.0
    assert agent.calls >= 1


@pytest.mark.asyncio
async def test_unbounded_budget_is_still_supported():
    """max_elapsed=None (the default) leaves the per-attempt timeout as
    the only clock, exactly as before."""
    obs = RecordingObserver()
    agent = FlakyAgent(fail_times=1)
    assert agent.retry.max_elapsed is None
    assert await agent.run("company", obs) == "ok:company:2"


class ScriptedKindAgent(Agent[str, str]):
    """Fails with a scripted sequence of error kinds, then succeeds."""

    name = "ScriptedKindAgent"

    def __init__(self, messages: list[str], retry: RetryPolicy) -> None:
        self._messages = messages
        self.retry = retry
        self.calls = 0

    async def execute(self, request: str) -> str:
        self.calls += 1
        if self.calls <= len(self._messages):
            raise Exception(self._messages[self.calls - 1])  # noqa: TRY002
        return f"ok:{self.calls}"


_MIXED_POLICY = RetryPolicy(
    max_attempts=3,
    rate_limit_max_attempts=6,
    initial_delay=0.0,
    rate_limit_initial_delay=0.0,
    max_delay=0.0,
    rate_limit_max_delay=0.0,
    jitter=0.0,
)


@pytest.mark.asyncio
async def test_rate_limit_attempts_do_not_consume_the_ordinary_budget():
    """Regression: budgets are counted per ErrorKind, not globally.

    With one shared counter, 429/429 advanced it to 3, so the first
    validation failure was refused at `3 < max_attempts(3)` and the
    ReportCompiler got ZERO revision attempts. A quota event must not
    cost a step the retries it needs for its actual work -- that would
    make the compiler's revision loop collateral damage of the very fix
    meant to protect it.
    """
    obs = RecordingObserver()
    agent = ScriptedKindAgent(
        [
            "429 RESOURCE_EXHAUSTED",
            "429 RESOURCE_EXHAUSTED",
            "validation failed: Section 1 missing",
            "validation failed: Section 2 missing",
        ],
        _MIXED_POLICY,
    )
    result = await agent.run("company", obs)

    assert result == "ok:5"
    kinds = [r[2] for r in obs.retries]
    assert kinds == [
        ErrorKind.RATE_LIMIT,
        ErrorKind.RATE_LIMIT,
        ErrorKind.INVALID_OUTPUT,
        ErrorKind.INVALID_OUTPUT,
    ]


@pytest.mark.asyncio
async def test_each_kind_exhausts_its_own_budget_independently():
    obs = RecordingObserver()
    # 6 rate-limit attempts and 3 invalid-output attempts are available;
    # the run spends 2 of the former and all 3 of the latter.
    agent = ScriptedKindAgent(
        ["429 RESOURCE_EXHAUSTED", "429 RESOURCE_EXHAUSTED"]
        + ["validation failed: nope"] * 10,
        _MIXED_POLICY,
    )
    with pytest.raises(AgentError) as exc_info:
        await agent.run("company", obs)

    assert exc_info.value.kind == ErrorKind.INVALID_OUTPUT
    # 2 rate-limit + 3 invalid-output attempts.
    assert agent.calls == 5
    assert exc_info.value.attempts == 5
    kinds = [r[2] for r in obs.retries]
    assert kinds.count(ErrorKind.RATE_LIMIT) == 2
    assert kinds.count(ErrorKind.INVALID_OUTPUT) == 2


@pytest.mark.asyncio
async def test_rate_limit_backoff_starts_at_the_bottom_of_its_own_ladder():
    """The wait is indexed by the kind's own attempt count.

    A 429 arriving as the third overall attempt must still wait
    rate_limit_initial_delay, not jump part-way up the ladder (or
    straight to the cap) because two unrelated failures preceded it.
    """
    obs = RecordingObserver()
    agent = ScriptedKindAgent(
        [
            "Connection reset by peer",
            "Connection reset by peer",
            "429 RESOURCE_EXHAUSTED",
            "429 RESOURCE_EXHAUSTED",
        ],
        RetryPolicy(
            max_attempts=3,
            rate_limit_max_attempts=6,
            initial_delay=0.001,
            max_delay=1.0,
            rate_limit_initial_delay=0.004,
            rate_limit_max_delay=1.0,
            jitter=0.0,
        ),
    )
    assert await agent.run("company", obs) == "ok:5"
    delays = [round(r[3], 6) for r in obs.retries]
    # TRANSIENT ladder: 0.001, 0.002. Then the RATE_LIMIT ladder restarts
    # at its own initial delay: 0.004, 0.008.
    assert delays == [0.001, 0.002, 0.004, 0.008]


@pytest.mark.asyncio
async def test_permanent_failure_logs_attempts_by_kind(caplog):
    """A step that burned its rate-limit budget before dying on something
    else must say so. Logging only the final kind is how the quota
    problem behind this change stayed invisible in the first place.
    """
    obs = RecordingObserver()
    agent = ScriptedKindAgent(
        ["429 RESOURCE_EXHAUSTED", "429 RESOURCE_EXHAUSTED"]
        + ["validation failed: nope"] * 10,
        _MIXED_POLICY,
    )
    with caplog.at_level("ERROR"), pytest.raises(AgentError):
        await agent.run("company", obs)
    messages = [r.message for r in caplog.records]
    assert any("attempts_by_kind=" in m for m in messages)
    assert any("RATE_LIMIT=2" in m for m in messages)
    assert any("INVALID_OUTPUT=3" in m for m in messages)


@pytest.mark.asyncio
async def test_cancellation_is_never_retried_or_wrapped():
    """CancelledError is a BaseException, not an Exception: it must
    propagate untouched rather than being classified and retried, exactly
    as the original `except Exception` loop allowed.
    """

    class Cancelled(Agent[str, str]):
        name = "Cancelled"
        retry = RetryPolicy(
            max_attempts=5,
            rate_limit_max_attempts=9,
            initial_delay=0.0,
            jitter=0.0,
        )

        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, request: str) -> str:
            self.calls += 1
            raise asyncio.CancelledError()

    obs = RecordingObserver()
    agent = Cancelled()
    with pytest.raises(asyncio.CancelledError):
        await agent.run("company", obs)
    assert agent.calls == 1
    assert obs.retries == []
