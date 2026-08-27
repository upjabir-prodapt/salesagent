"""Tests for src/worker/domain/contracts.py."""

from __future__ import annotations

import pytest

from src.shared.exceptions import AgentOutputError
from src.worker.domain.contracts import (
    DOMAIN_OUTPUT_KEYS,
    get_agent_contract,
    get_output_key,
    is_tracked_agent,
    list_missing_domain_outputs,
    list_missing_research_outputs,
    validate_agent_output,
    validate_domain_outputs_present,
    validate_research_outputs_complete,
)


def test_get_agent_contract_known_and_unknown():
    assert get_agent_contract("ReportCompiler") is not None
    assert get_agent_contract("NotARealAgent") is None


def test_get_output_key_returns_none_for_unknown_agent():
    assert get_output_key("QueryGeneratorAgent") == "query_generator_output"
    assert get_output_key("Unknown") is None


def test_is_tracked_agent():
    assert is_tracked_agent("ReportCompiler") is True
    assert is_tracked_agent("RandomAgent") is False


def test_list_missing_research_outputs_reports_missing_required():
    state: dict = {}
    missing = list_missing_research_outputs(state)
    assert "QueryGeneratorAgent" in missing


def test_list_missing_research_outputs_empty_when_populated():
    state = {"query_generator_output": "some queries"}
    assert list_missing_research_outputs(state) == []


def test_list_missing_domain_outputs_all_missing_on_empty_state():
    missing = list_missing_domain_outputs({})
    assert set(missing) == set(DOMAIN_OUTPUT_KEYS)


def test_list_missing_domain_outputs_none_missing_when_all_populated():
    state = dict.fromkeys(DOMAIN_OUTPUT_KEYS, "content")
    assert list_missing_domain_outputs(state) == []


def test_validate_domain_outputs_present_raises_below_minimum():
    state = dict.fromkeys(list(DOMAIN_OUTPUT_KEYS)[:2], "content")
    with pytest.raises(AgentOutputError) as exc_info:
        validate_domain_outputs_present(state, minimum=6)
    assert exc_info.value.error_class == "RESEARCH_DATA_MISSING"


def test_validate_domain_outputs_present_passes_at_minimum():
    state = dict.fromkeys(list(DOMAIN_OUTPUT_KEYS)[:6], "content")
    validate_domain_outputs_present(state, minimum=6)  # should not raise


def test_validate_domain_outputs_present_fail_fast_false_uses_missing_output_class():
    state: dict = {}
    with pytest.raises(AgentOutputError) as exc_info:
        validate_domain_outputs_present(state, minimum=6, fail_fast=False)
    assert exc_info.value.error_class == "MISSING_OUTPUT"


def test_validate_agent_output_raises_for_missing_required_output():
    with pytest.raises(AgentOutputError):
        validate_agent_output({}, "ReportCompiler")


def test_validate_agent_output_noop_for_untracked_agent():
    validate_agent_output({}, "SomeUntrackedAgent")  # should not raise


def test_validate_agent_output_noop_when_populated():
    state = {"final_report": "# Report"}
    validate_agent_output(state, "ReportCompiler")  # should not raise


def test_validate_research_outputs_complete_raises_with_missing():
    with pytest.raises(AgentOutputError) as exc_info:
        validate_research_outputs_complete({})
    assert exc_info.value.agent_name == "AlignmentAnalyst"


def test_validate_research_outputs_complete_passes_when_all_present():
    state = {"query_generator_output": "queries"}
    validate_research_outputs_complete(state)  # should not raise
