from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.agents import LlmAgent

from src.core.config import settings
from src.core.exceptions import AgentOutputError
from src.services.research.agent.utils.retry_state import AGENT_RETRY_COUNTS_KEY
from src.services.research.agent.utils.retrying_llm_agent import RetryingLlmAgent


@pytest.mark.asyncio
async def test_retrying_llm_agent_retries_leaf_once_then_succeeds(monkeypatch):
    attempts = {"count": 0}

    async def flaky_run(self, ctx):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient model failure")
        yield "ok-event"

    monkeypatch.setattr(LlmAgent, "_run_async_impl", flaky_run)
    monkeypatch.setattr(settings, "AGENT_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(settings, "AGENT_RETRY_WAIT_FIXED", 0)

    state: dict[str, object] = {}
    ctx = SimpleNamespace(session=SimpleNamespace(state=state))
    agent = RetryingLlmAgent(
        name="ExecutiveAgent",
        model="gemini-2.5-flash",
        instruction="test",
        output_key="executiveagent_output",
    )

    events = []
    async for event in agent._run_async_impl(ctx):
        events.append(event)

    assert events == ["ok-event"]
    assert attempts["count"] == 2
    assert state[AGENT_RETRY_COUNTS_KEY]["ExecutiveAgent"] == 1
    assert "agent_retry_hints" not in state


@pytest.mark.asyncio
async def test_retrying_llm_agent_raises_agent_output_error_when_exhausted(monkeypatch):
    async def always_fail(self, ctx):
        raise RuntimeError("persistent transport failure")
        yield "never"

    monkeypatch.setattr(LlmAgent, "_run_async_impl", always_fail)
    monkeypatch.setattr(settings, "AGENT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "AGENT_RETRY_WAIT_FIXED", 0)

    state: dict[str, object] = {}
    ctx = SimpleNamespace(session=SimpleNamespace(state=state))
    agent = RetryingLlmAgent(
        name="ExecutiveAgent",
        model="gemini-2.5-flash",
        instruction="test",
        output_key="executiveagent_output",
    )

    with pytest.raises(AgentOutputError) as exc_info:
        async for _ in agent._run_async_impl(ctx):
            pass

    assert exc_info.value.error_class in {"MODEL_ERROR", "AGENT_ERROR"}
    assert exc_info.value.output_key == "executiveagent_output"
    assert isinstance(exc_info.value.__cause__, RuntimeError)
