"""Typed contracts for tracked research agents and state outputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from ....core.exceptions import AgentOutputError


@dataclass(frozen=True)
class AgentContract:
    """Stable contract between an agent name and its output state key."""

    agent_name: str
    output_key: str
    required: bool = True


AGENT_CONTRACTS: Final[tuple[AgentContract, ...]] = (
    AgentContract("FirmographicsAgent", "firmographicsagent_output"),
    AgentContract("GeographicAgent", "geographicagent_output"),
    AgentContract("ExecutiveAgent", "executiveagent_output"),
    AgentContract("StrategyAgent", "strategyagent_output"),
    AgentContract("ComplianceAgent", "complianceagent_output"),
    AgentContract("MarketAgent", "marketagent_output"),
    AgentContract("EcosystemAgent", "ecosystemagent_output"),
    AgentContract("TechStackAgent", "techstackagent_output"),
    AgentContract("ProcurementAgent", "procurementagent_output"),
    AgentContract("GrowthSignals", "growthsignals_output"),
    AgentContract("RiskSignals", "risksignals_output"),
    AgentContract("CampaignSignals", "campaignsignals_output"),
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


def validate_research_outputs_complete(state: dict[str, Any]) -> None:
    """Raise AgentOutputError if any parallel research output is missing."""
    missing = list_missing_research_outputs(state)
    if not missing:
        return
    first = missing[0]
    output_key = get_output_key(first) or f"{first.lower()}_output"
    raise AgentOutputError(
        (
            "Research phase incomplete before synthesis: missing outputs for "
            f"{', '.join(missing)}."
        ),
        agent_name="AlignmentAnalyst",
        output_key=output_key,
        error_class="MISSING_OUTPUT",
    )
