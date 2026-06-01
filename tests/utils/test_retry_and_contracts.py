import pytest
from google.adk.sessions.state import State

from src.core.exceptions import AgentOutputError
from src.services.research.runtime.retry.pipeline import (
    build_retry_continuation_message,
    get_output_key,
    validate_agent_output,
)
from src.services.research.runtime.retry.errors import (
    RETRY_SCOPE_NONE,
    RETRY_SCOPE_RUNNER_COLD,
    RETRY_SCOPE_RUNNER_WARM,
    retry_scope_for_error_class,
)
from src.services.research.runtime.retry.state import (
    apply_retry,
    clear_retry_flag,
    increment_retry_count,
    pop_retry_hint,
    prepare_agent_retry,
)
from src.services.research.runtime.state_mutation import requires_cold_retry


def test_validate_agent_output_contract():
    state = {"executiveagent_output": "ready"}
    validate_agent_output(state, "ExecutiveAgent")
    with pytest.raises(AgentOutputError) as exc_info:
        validate_agent_output({}, "ExecutiveAgent")
    assert exc_info.value.error_class == "MISSING_OUTPUT"
    assert exc_info.value.output_key == get_output_key("ExecutiveAgent")


def test_prepare_retry_state_contract():
    state = {
        "executiveagent_output": "stale",
        "agent_status_map": {"ExecutiveAgent": "completed"},
    }
    attempt = increment_retry_count(state, "ExecutiveAgent")
    assert attempt == 1
    prepare_agent_retry(state, "ExecutiveAgent")
    assert "executiveagent_output" not in state
    assert state["agent_status_map"]["ExecutiveAgent"] == "retrying"
    clear_retry_flag(state)
    assert "pipeline_retry_agent" not in state


def test_pop_retry_hint_works_on_adk_state_wrapper():
    adk_state = State(
        value={"agent_retry_hints": {"ExecutiveAgent": "retry me"}},
        delta={},
    )
    hint = pop_retry_hint(adk_state, "ExecutiveAgent")
    assert hint == "retry me"
    assert "agent_retry_hints" not in adk_state.to_dict()


def test_requires_cold_retry_contract():
    exc = AgentOutputError(
        "missing output",
        agent_name="ExecutiveAgent",
        output_key="executiveagent_output",
        error_class="MISSING_OUTPUT",
    )
    assert requires_cold_retry(exc) is True


def test_retry_continuation_message_contains_agent_and_company():
    message = build_retry_continuation_message("ExecutiveAgent", "Acme Corp")
    assert message.parts
    text = message.parts[0].text
    assert "ExecutiveAgent" in text
    assert "Acme Corp" in text


def test_retry_continuation_message_for_missing_output_mentions_output_key():
    message = build_retry_continuation_message(
        "ExecutiveAgent",
        "Acme Corp",
        output_key="executiveagent_output",
        error_class="MISSING_OUTPUT",
        reason="required output remained empty",
    )
    text = message.parts[0].text
    assert "executiveagent_output" in text
    assert "completed without populating required output_key" in text


def test_retry_scope_mapping_contract():
    assert retry_scope_for_error_class("MISSING_OUTPUT") == RETRY_SCOPE_RUNNER_COLD
    assert retry_scope_for_error_class("CONNECT_ERROR") == RETRY_SCOPE_RUNNER_COLD
    assert retry_scope_for_error_class("RESOURCE_EXHAUSTED") == RETRY_SCOPE_RUNNER_WARM
    assert retry_scope_for_error_class("AGENT_ERROR") == RETRY_SCOPE_RUNNER_WARM
    assert retry_scope_for_error_class("REPORT_VALIDATION_FAILED") == RETRY_SCOPE_NONE


@pytest.mark.asyncio
async def test_report_compiler_validation_failure_is_not_retried():
    state = {"report_validation_status": "FAILED", "final_report": "# report"}
    with pytest.raises(AgentOutputError) as exc_info:
        validate_agent_output(state, "ReportCompiler")

    exc = exc_info.value
    assert exc.error_class == "REPORT_VALIDATION_FAILED"
    assert requires_cold_retry(exc) is False
    assert retry_scope_for_error_class(exc.error_class) == RETRY_SCOPE_NONE

    allowed = await apply_retry(state, exc, on_retry=None)
    assert allowed is False
    assert state.get("agent_retry_counts", {}).get("ReportCompiler") is None


def test_report_compiler_missing_validation_status_is_blocked() -> None:
    state = {"final_report": "# report"}
    with pytest.raises(AgentOutputError) as exc_info:
        validate_agent_output(state, "ReportCompiler")

    assert exc_info.value.error_class == "REPORT_VALIDATION_FAILED"
    assert "never called" in str(exc_info.value)

