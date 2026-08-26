import pytest
from google.adk.sessions.state import State

from src.shared.exceptions import AgentOutputError
from src.worker.runtime.resilience.errors import (
    RETRY_SCOPE_LEAF_LOCAL,
    RETRY_SCOPE_NONE,
    RETRY_SCOPE_RUNNER_WARM,
    retry_scope_for_error_class,
)
from src.worker.runtime.resilience.runner_loop import (
    build_retry_continuation_message,
    get_output_key,
    validate_agent_output,
)
from src.worker.runtime.resilience.state import (
    clear_retry_flag,
    increment_retry_count,
    pop_retry_hint,
    prepare_agent_retry,
)
from src.worker.runtime.state_mutation import requires_cold_retry


def test_validate_agent_output_contract():
    state = {"alignment_output": "ready"}
    validate_agent_output(state, "AlignmentAnalyst")
    with pytest.raises(AgentOutputError) as exc_info:
        validate_agent_output({}, "AlignmentAnalyst")
    assert exc_info.value.error_class == "MISSING_OUTPUT"
    assert exc_info.value.output_key == get_output_key("AlignmentAnalyst")


def test_prepare_retry_state_contract():
    state = {
        "alignment_output": "stale",
        "agent_status_map": {"AlignmentAnalyst": "completed"},
    }
    attempt = increment_retry_count(state, "AlignmentAnalyst")
    assert attempt == 1
    prepare_agent_retry(state, "AlignmentAnalyst")
    assert "alignment_output" not in state
    assert state["agent_status_map"]["AlignmentAnalyst"] == "retrying"
    clear_retry_flag(state)
    assert "pipeline_retry_agent" not in state


def test_pop_retry_hint_works_on_adk_state_wrapper():
    adk_state = State(
        value={"agent_retry_hints": {"AlignmentAnalyst": "retry me"}},
        delta={},
    )
    hint = pop_retry_hint(adk_state, "AlignmentAnalyst")
    assert hint == "retry me"
    assert "agent_retry_hints" not in adk_state.to_dict()


def test_requires_cold_retry_false_for_missing_output():
    exc = AgentOutputError(
        "missing output",
        agent_name="AlignmentAnalyst",
        output_key="alignment_output",
        error_class="MISSING_OUTPUT",
    )
    assert requires_cold_retry(exc) is False


def test_retry_continuation_message_contains_agent_and_company():
    message = build_retry_continuation_message("AlignmentAnalyst", "Acme Corp")
    assert message.parts
    text = message.parts[0].text
    assert "AlignmentAnalyst" in text
    assert "Acme Corp" in text


def test_retry_continuation_message_for_missing_output_mentions_output_key():
    message = build_retry_continuation_message(
        "AlignmentAnalyst",
        "Acme Corp",
        output_key="alignment_output",
        error_class="MISSING_OUTPUT",
        reason="required output remained empty",
    )
    text = message.parts[0].text
    assert "alignment_output" in text
    assert "completed without populating required output_key" in text


def test_retry_scope_mapping_contract():
    assert retry_scope_for_error_class("MISSING_OUTPUT") == RETRY_SCOPE_LEAF_LOCAL
    assert retry_scope_for_error_class("CONNECT_ERROR") == RETRY_SCOPE_LEAF_LOCAL
    assert retry_scope_for_error_class("RESOURCE_EXHAUSTED") == RETRY_SCOPE_RUNNER_WARM
    assert retry_scope_for_error_class("AGENT_ERROR") == RETRY_SCOPE_RUNNER_WARM
    assert retry_scope_for_error_class("REPORT_VALIDATION_FAILED") == RETRY_SCOPE_NONE


@pytest.mark.asyncio
async def test_report_compiler_validation_failure_with_report_is_allowed():
    state = {"report_validation_status": "FAILED", "final_report": "# report"}
    validate_agent_output(state, "ReportCompiler")


def test_report_compiler_missing_validation_status_is_allowed() -> None:
    state = {"final_report": "# report"}
    validate_agent_output(state, "ReportCompiler")


def test_report_compiler_still_blocks_missing_final_report() -> None:
    state = {"report_validation_status": "FAILED"}
    with pytest.raises(AgentOutputError) as exc_info:
        validate_agent_output(state, "ReportCompiler")

    assert exc_info.value.error_class == "MISSING_OUTPUT"
