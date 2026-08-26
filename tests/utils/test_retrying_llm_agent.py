from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent

from src.shared.config import settings
from src.shared.exceptions import AgentOutputError
from src.worker.agents.retrying_agent import RetryingLlmAgent
from src.worker.runtime.resilience.state import AGENT_RETRY_COUNTS_KEY


@pytest.mark.asyncio
async def test_retrying_llm_agent_retries_leaf_once_then_succeeds(monkeypatch):
    attempts = {"count": 0}

    async def flaky_run(self, ctx):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient model failure")
        ctx.session.state["alignment_output"] = "done"
        yield "ok-event"

    monkeypatch.setattr(LlmAgent, "_run_async_impl", flaky_run)
    monkeypatch.setattr(settings, "AGENT_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "AGENT_RETRY_WAIT_FIXED", 0)

    state: dict[str, object] = {"alignment_output": "done"}
    ctx = SimpleNamespace(
        session=SimpleNamespace(state=state, events=[]),
        invocation_id="inv-1",
    )
    ctx.set_agent_state = lambda *args, **kwargs: None
    agent = RetryingLlmAgent(
        name="AlignmentAnalyst",
        model="gemini-2.5-flash",
        instruction="test",
        output_key="alignment_output",
    )
    agent._create_agent_state_event = lambda ctx: "reset-event"

    events = []
    async for event in agent._run_async_impl(ctx):
        events.append(event)

    assert "ok-event" in events
    assert attempts["count"] == 2
    assert state[AGENT_RETRY_COUNTS_KEY]["AlignmentAnalyst"] == 1
    assert "agent_retry_hints" not in state


@pytest.mark.asyncio
async def test_retrying_llm_agent_retries_missing_output_then_succeeds(monkeypatch):
    attempts = {"count": 0}

    async def empty_then_ok(self, ctx):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return
            yield  # pragma: no cover
        state = ctx.session.state
        state["alignment_output"] = "final answer"
        return
        yield  # pragma: no cover

    monkeypatch.setattr(LlmAgent, "_run_async_impl", empty_then_ok)
    monkeypatch.setattr(settings, "AGENT_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "AGENT_RETRY_WAIT_FIXED", 0)

    state: dict[str, object] = {}
    ctx = SimpleNamespace(
        session=SimpleNamespace(state=state, events=[]),
        invocation_id="inv-2",
    )
    ctx.set_agent_state = lambda *args, **kwargs: None
    agent = RetryingLlmAgent(
        name="AlignmentAnalyst",
        model="gemini-2.5-flash",
        instruction="test",
        output_key="alignment_output",
    )
    agent._create_agent_state_event = lambda ctx: "reset-event"

    events = []
    async for event in agent._run_async_impl(ctx):
        events.append(event)

    assert state["alignment_output"] == "final answer"
    assert attempts["count"] == 2
    assert "reset-event" in events


@pytest.mark.asyncio
async def test_retrying_llm_agent_raises_agent_output_error_when_exhausted(monkeypatch):
    async def always_fail(self, ctx):
        raise RuntimeError("persistent transport failure")
        yield "never"

    monkeypatch.setattr(LlmAgent, "_run_async_impl", always_fail)
    monkeypatch.setattr(settings, "AGENT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "AGENT_RETRY_WAIT_FIXED", 0)

    state: dict[str, object] = {}
    ctx = SimpleNamespace(
        session=SimpleNamespace(state=state, events=[]),
        invocation_id="inv-3",
    )
    ctx.set_agent_state = lambda *args, **kwargs: None
    agent = RetryingLlmAgent(
        name="AlignmentAnalyst",
        model="gemini-2.5-flash",
        instruction="test",
        output_key="alignment_output",
    )
    agent._create_agent_state_event = lambda ctx: "reset-event"

    with pytest.raises(AgentOutputError) as exc_info:
        async for _ in agent._run_async_impl(ctx):
            pass

    assert exc_info.value.error_class in {"MODEL_ERROR", "AGENT_ERROR"}
    assert exc_info.value.output_key == "alignment_output"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
