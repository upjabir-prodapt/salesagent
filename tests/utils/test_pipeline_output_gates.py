import pytest

from src.core.config import settings
from src.core.exceptions import AgentOutputError
from src.services.research.agent.utils.agent_pipeline import (
    AGENT_OUTPUT_KEYS,
    get_retry_count,
    increment_retry_count,
    max_retries_exceeded,
    prepare_agent_retry,
    validate_agent_output,
)


def test_all_research_agents_registered():
    expected = {
        "FirmographicsAgent",
        "GeographicAgent",
        "ExecutiveAgent",
        "AlignmentAnalyst",
        "ReportCompiler",
    }
    assert expected.issubset(AGENT_OUTPUT_KEYS.keys())


def test_validate_agent_output_raises_when_missing():
    with pytest.raises(AgentOutputError) as exc_info:
        validate_agent_output({}, "AlignmentAnalyst")
    assert exc_info.value.output_key == "alignment_output"


def test_validate_agent_output_passes_with_content():
    validate_agent_output(
        {"alignment_output": '{"alignment_mappings": []}'},
        "AlignmentAnalyst",
    )


def test_prepare_agent_retry_clears_output():
    state = {
        "alignment_output": "data",
        "agent_status_map": {"AlignmentAnalyst": "completed"},
    }
    prepare_agent_retry(state, "AlignmentAnalyst")
    assert "alignment_output" not in state
    assert state["agent_status_map"]["AlignmentAnalyst"] == "retrying"


def test_max_retries_respects_attempt_budget():
    state: dict = {}
    assert not max_retries_exceeded(state, "FirmographicsAgent")
    for _ in range(settings.AGENT_RETRY_ATTEMPTS - 1):
        increment_retry_count(state, "FirmographicsAgent")
    assert max_retries_exceeded(state, "FirmographicsAgent")
