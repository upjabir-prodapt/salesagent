from __future__ import annotations

import pytest

from src.shared.exceptions import AgentOutputError
from src.worker.domain.contracts import (
    list_missing_research_outputs,
    validate_research_outputs_complete,
)
from src.worker.runtime.resilience.errors import (
    RETRY_SCOPE_LEAF_LOCAL,
    resolve_retry_agents,
    retry_scope_for_error_class,
)


def test_list_missing_research_outputs():
    state = {"query_generator_output": "ok"}
    missing = list_missing_research_outputs(state)
    assert "QueryGeneratorAgent" not in missing

    empty_state = {}
    missing_empty = list_missing_research_outputs(empty_state)
    assert "QueryGeneratorAgent" in missing_empty


def test_validate_research_outputs_complete_raises():
    with pytest.raises(AgentOutputError) as exc_info:
        validate_research_outputs_complete({})
    assert exc_info.value.error_class == "MISSING_OUTPUT"
    assert exc_info.value.agent_name == "AlignmentAnalyst"


def test_resolve_retry_agents_alignment_missing_research():
    state = {}
    exc = AgentOutputError(
        "gate",
        agent_name="AlignmentAnalyst",
        output_key="alignment_output",
        error_class="MISSING_OUTPUT",
    )
    agents = resolve_retry_agents(exc, state)
    assert "QueryGeneratorAgent" in agents


def test_resolve_retry_agents_alignment_research_ok():
    state = {
        contract.output_key: "x"
        for contract in __import__(
            "src.worker.domain.agent_contracts",
            fromlist=["RESEARCH_AGENT_CONTRACTS"],
        ).RESEARCH_AGENT_CONTRACTS
    }
    exc = AgentOutputError(
        "missing alignment",
        agent_name="AlignmentAnalyst",
        output_key="alignment_output",
        error_class="MISSING_OUTPUT",
    )
    assert resolve_retry_agents(exc, state) == ["AlignmentAnalyst"]


def test_missing_output_scope_is_leaf_local():
    assert retry_scope_for_error_class("MISSING_OUTPUT") == RETRY_SCOPE_LEAF_LOCAL
