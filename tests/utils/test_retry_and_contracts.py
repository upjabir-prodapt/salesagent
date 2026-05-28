import pytest

from src.core.exceptions import AgentOutputError
from src.services.research.agent.utils.agent_contracts import (
    get_output_key,
    validate_agent_output,
)
from src.services.research.agent.utils.agent_pipeline import (
    build_retry_continuation_message,
)
from src.services.research.agent.utils.retry_errors import (
    RETRY_SCOPE_RUNNER_COLD,
    RETRY_SCOPE_RUNNER_WARM,
    retry_scope_for_error_class,
)
from src.services.research.agent.utils.retry_state import (
    clear_retry_flag,
    increment_retry_count,
    prepare_agent_retry,
)
from src.services.research.session_state_mutator import requires_cold_retry


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

