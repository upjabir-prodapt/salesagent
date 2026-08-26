"""Agent output contracts and validation rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from src.shared.exceptions import AgentOutputError


@dataclass(frozen=True)
class AgentContract:
    """Stable contract between an agent name and its output state key."""

    agent_name: str
    output_key: str
    required: bool = True


AGENT_CONTRACTS: Final[tuple[AgentContract, ...]] = (
    AgentContract("QueryGeneratorAgent", "query_generator_output"),
    AgentContract("ParallelSearchAgent", "parallel_search_output", required=False),
    AgentContract("AlignmentAnalyst", "alignment_output"),
    AgentContract("ReportCompiler", "final_report"),
)

RESEARCH_AGENT_CONTRACTS: Final[tuple[AgentContract, ...]] = tuple(
    contract
    for contract in AGENT_CONTRACTS
    if contract.agent_name not in ("AlignmentAnalyst", "ReportCompiler")
)

SYNTHESIS_AGENT_NAMES: Final[frozenset[str]] = frozenset(
    {"AlignmentAnalyst", "ReportCompiler"}
)

AGENT_OUTPUT_KEYS: Final[dict[str, str]] = {
    contract.agent_name: contract.output_key for contract in AGENT_CONTRACTS
}

DOMAIN_OUTPUT_KEYS: Final[tuple[str, ...]] = (
    "firmographicsagent_output",
    "geographicagent_output",
    "executiveagent_output",
    "strategyagent_output",
    "complianceagent_output",
    "marketagent_output",
    "ecosystemagent_output",
    "techstackagent_output",
    "procurementagent_output",
    "growthsignals_output",
    "risksignals_output",
    "campaignsignals_output",
)

MIN_DOMAIN_OUTPUTS_REQUIRED: Final[int] = 6


def get_agent_contract(agent_name: str) -> AgentContract | None:
    """Return the typed contract for an agent, if tracked."""
    for contract in AGENT_CONTRACTS:
        if contract.agent_name == agent_name:
            return contract
    return None


def get_output_key(agent_name: str) -> str | None:
    """Compatibility helper for legacy callsites."""
    contract = get_agent_contract(agent_name)
    return contract.output_key if contract else None


def is_tracked_agent(agent_name: str) -> bool:
    """Whether an agent participates in output contract validation."""
    return get_agent_contract(agent_name) is not None


def _output_nonempty(state: dict[str, Any], output_key: str) -> bool:
    value = state.get(output_key)
    return value is not None and bool(str(value).strip())


def list_missing_research_outputs(state: dict[str, Any]) -> list[str]:
    """Return research agent names with empty required output_key in *state*."""
    missing: list[str] = []
    for contract in RESEARCH_AGENT_CONTRACTS:
        if contract.required and not _output_nonempty(state, contract.output_key):
            missing.append(contract.agent_name)
    return missing


def list_missing_domain_outputs(state: dict[str, Any]) -> list[str]:
    """Return per-domain output keys that are absent or empty in *state*."""
    return [key for key in DOMAIN_OUTPUT_KEYS if not _output_nonempty(state, key)]


def validate_domain_outputs_present(
    state: dict[str, Any],
    *,
    minimum: int | None = None,
    fail_fast: bool = True,
) -> None:
    """Raise AgentOutputError when too few per-domain research outputs exist."""
    required = MIN_DOMAIN_OUTPUTS_REQUIRED if minimum is None else minimum
    missing = list_missing_domain_outputs(state)
    populated = len(DOMAIN_OUTPUT_KEYS) - len(missing)
    if populated >= required:
        return
    raise AgentOutputError(
        (
            f"Research phase produced only {populated}/{len(DOMAIN_OUTPUT_KEYS)} "
            f"domain outputs (minimum {required}). "
            f"Missing: {', '.join(missing)}."
        ),
        agent_name="ParallelSearchAgent",
        output_key="parallel_search_output",
        error_class="RESEARCH_DATA_MISSING" if fail_fast else "MISSING_OUTPUT",
    )


def validate_agent_output(state: dict[str, Any], agent_name: str) -> None:
    """Validate that a completed tracked agent produced its required output key."""
    contract = get_agent_contract(agent_name)
    if not contract or not contract.required:
        return
    if not _output_nonempty(state, contract.output_key):
        raise AgentOutputError(
            f"Agent '{agent_name}' finished without populating required output '{contract.output_key}'.",
            agent_name=agent_name,
            output_key=contract.output_key,
            error_class="MISSING_OUTPUT",
        )


def validate_research_outputs_complete(state: dict[str, Any]) -> None:
    """Raise AgentOutputError if any parallel research output is missing."""
    missing = list_missing_research_outputs(state)
    if not missing:
        return
    first = missing[0]
    output_key = get_output_key(first) or f"{first.lower()}_output"
    raise AgentOutputError(
        f"Research phase incomplete before synthesis: missing outputs for {', '.join(missing)}.",
        agent_name="AlignmentAnalyst",
        output_key=output_key,
        error_class="MISSING_OUTPUT",
    )


__all__ = [
    "AgentContract",
    "AGENT_CONTRACTS",
    "RESEARCH_AGENT_CONTRACTS",
    "SYNTHESIS_AGENT_NAMES",
    "AGENT_OUTPUT_KEYS",
    "DOMAIN_OUTPUT_KEYS",
    "MIN_DOMAIN_OUTPUTS_REQUIRED",
    "get_agent_contract",
    "get_output_key",
    "is_tracked_agent",
    "list_missing_research_outputs",
    "list_missing_domain_outputs",
    "validate_domain_outputs_present",
    "validate_research_outputs_complete",
    "validate_agent_output",
]
