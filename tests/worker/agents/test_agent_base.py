"""Unit tests for the Agent template method in src/worker/agents/base.py.

These tests verify the core requirement: when a step's execute() fails,
only that step retries (in-place, via its own RetryPolicy), with no
shared state or cross-step effects.
"""

from __future__ import annotations

import asyncio

import pytest

from src.worker.agents.base import (
    Agent,
    AgentError,
    ErrorKind,
    InvalidOutputError,
    RetryPolicy,
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
