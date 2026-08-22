from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.adk.agents.run_config import RunConfig
from google.genai import types

from src.core.exceptions import AgentOutputError
from src.services.research.run.resilience.runner_loop import (
    run_runner_with_per_agent_retry,
)


class _DummyRunner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run_async(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise ExceptionGroup(
                "parallel-failure",
                [
                    AgentOutputError(
                        "AlignmentAnalyst output missing",
                        agent_name="AlignmentAnalyst",
                        output_key="alignment_output",
                        error_class="MISSING_OUTPUT",
                    )
                ],
            )
        yield SimpleNamespace(
            invocation_id="inv-2",
            error_code=None,
            error_message=None,
            author="AlignmentAnalyst",
        )


@pytest.mark.asyncio
async def test_run_runner_with_per_agent_retry_unwraps_exception_group():
    runner = _DummyRunner()
    state: dict[str, object] = {}
    retry_calls: list[tuple[str, int]] = []
    processed_events: list[object] = []

    async def _get_session_state() -> dict[str, object]:
        return state

    def _persist_state(mutator) -> bool:
        mutator(state)
        return True

    async def _on_retry(agent_name: str, attempt: int) -> None:
        retry_calls.append((agent_name, attempt))

    async def _process_event(event) -> None:
        processed_events.append(event)

    await run_runner_with_per_agent_retry(
        runner,
        app_name="sales_research_app",
        user_id="api_user",
        session_id="session-1",
        run_config=RunConfig(),
        new_message=types.UserContent(parts=[types.Part(text="start")]),
        process_event=_process_event,
        get_session_state=_get_session_state,
        on_retry=_on_retry,
        persist_state=_persist_state,
        company_name="Acme Corp",
    )

    assert len(runner.calls) == 2
    assert retry_calls == [("AlignmentAnalyst", 1)]
    assert len(processed_events) == 1

    second_call = runner.calls[1]
    assert "invocation_id" not in second_call
    continuation_message = second_call["new_message"].parts[0].text
    assert "alignment_output" in continuation_message
    assert "completed without populating required output_key" in continuation_message
