"""Integration tests for AdkAgentStep: driving a single ADK LlmAgent from
the outer Agent.run() retry loop, with a fresh session per attempt.

This is the regression test proving the design decision in
IMPLEMENTATION_PLAN.md section 2: keep Google ADK, but stop sharing one
root agent's session state across sub-agents. Each step here builds and
tears down its own single-agent Runner per attempt.
"""

from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from src.worker.agents.base import AdkAgentStep, AgentError, ErrorKind, RetryPolicy
from src.worker.observers import Observer


class RecordingObserver(Observer):
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


def _text_response(text: str, *, input_tokens: int = 10, output_tokens: int = 5):
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=input_tokens,
            candidates_token_count=output_tokens,
        ),
    )


class _CallCounter:
    """Plain mutable counter shared via closure, since BaseLlm is a frozen-
    by-convention Pydantic model and re-instantiated per Agent instance."""

    def __init__(self) -> None:
        self.count = 0


class FlakyLlm(BaseLlm):
    """Fails N times with a retryable-looking error, then succeeds."""

    model: str = "fake-flaky"
    fail_times: int = 0

    def model_post_init(self, __context) -> None:
        object.__setattr__(self, "_counter", _CallCounter())

    @property
    def calls(self) -> int:
        return self._counter.count  # type: ignore[attr-defined]

    async def generate_content_async(self, llm_request, stream: bool = False):
        self._counter.count += 1  # type: ignore[attr-defined]
        if self._counter.count <= self.fail_times:  # type: ignore[attr-defined]
            raise Exception(  # noqa: TRY002
                f"429 rate limit on attempt {self._counter.count}"  # type: ignore[attr-defined]
            )
        yield _text_response("OK-RESULT")


class AlwaysSafetyBlockedLlm(BaseLlm):
    model: str = "fake-safety"

    async def generate_content_async(self, llm_request, stream: bool = False):
        if False:  # pragma: no cover - makes this an async generator
            yield None
        raise Exception("Response blocked_reason=SAFETY HARM_CATEGORY_HARASSMENT")  # noqa: TRY002


class _EchoStep(AdkAgentStep[str, str]):
    """Minimal concrete AdkAgentStep for testing: echoes input back."""

    name = "EchoStep"
    retry = RetryPolicy(max_attempts=3, initial_delay=0.001, jitter=0.0)

    def __init__(self, llm: BaseLlm) -> None:
        self._llm = llm

    def build_agent(self) -> LlmAgent:
        return LlmAgent(
            name=self.name,
            model=self._llm,
            instruction="echo",
            include_contents="none",
            output_key="echo_output",
        )

    def to_input(self, request: str) -> str:
        return request

    def to_output(self, raw, usage) -> str:
        return f"{raw}|tokens={usage[0]}:{usage[1]}"


@pytest.mark.asyncio
async def test_adk_agent_step_recovers_from_transient_failure():
    obs = RecordingObserver()
    step = _EchoStep(FlakyLlm(fail_times=2))

    result = await step.run("hello", obs)

    assert result == "OK-RESULT|tokens=10:5"
    assert len(obs.retries) == 2
    assert len(obs.successes) == 1
    assert obs.successes[0][1] == 3  # succeeded on the 3rd attempt


@pytest.mark.asyncio
async def test_adk_agent_step_fresh_session_per_attempt_no_leakage():
    """Each retry attempt must start from a clean session -- no warm resume,
    no stale agent state carried across attempts.
    """
    obs = RecordingObserver()
    llm = FlakyLlm(fail_times=1)
    step = _EchoStep(llm)

    result = await step.run("first-request", obs)

    assert result == "OK-RESULT|tokens=10:5"
    # Two LLM calls total: attempt 1 (failed) + attempt 2 (succeeded).
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_adk_agent_step_safety_block_never_retries():
    obs = RecordingObserver()
    step = _EchoStep(AlwaysSafetyBlockedLlm())

    with pytest.raises(AgentError) as exc_info:
        await step.run("hello", obs)

    assert exc_info.value.kind == ErrorKind.SAFETY
    assert exc_info.value.attempts == 1
    assert len(obs.retries) == 0


@pytest.mark.asyncio
async def test_adk_agent_step_exhausts_budget_raises_agent_error():
    obs = RecordingObserver()
    step = _EchoStep(FlakyLlm(fail_times=99))

    with pytest.raises(AgentError) as exc_info:
        await step.run("hello", obs)

    assert exc_info.value.attempts == 3
    assert exc_info.value.kind == ErrorKind.RATE_LIMIT


@pytest.mark.asyncio
async def test_two_adk_agent_steps_are_fully_independent():
    """Regression test for bug A1: a failing step must never consume or
    interfere with another step's retry budget.
    """
    obs = RecordingObserver()
    step_a = _EchoStep(FlakyLlm(fail_times=2))
    step_b = _EchoStep(FlakyLlm(fail_times=0))

    result_b = await step_b.run("b-input", obs)
    assert result_b == "OK-RESULT|tokens=10:5"

    result_a = await step_a.run("a-input", obs)
    assert result_a == "OK-RESULT|tokens=10:5"
    assert len([r for r in obs.retries if r[0] == "EchoStep"]) == 2
