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
    AgentContract("QueryGeneratorAgent", "query_generator_output"),
    AgentContract("ResearchSynthesizer", "research_synthesizer_output"),
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

# Per-domain state keys written by ResearchSynthesizer and read by the
# synthesis agents (AlignmentAnalyst, ReportCompiler) via {key?} injection.
# These are not agent-scoped contracts -- one agent produces all of them --
# so they are gated separately by validate_domain_outputs_present().
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

# Below this many populated domains the report degrades into a page of
# "Data not available from research.", so fail loudly and let the retry
# machinery re-run ResearchSynthesizer instead of shipping an empty report.
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
    """Raise AgentOutputError when too few per-domain research outputs exist.

    Guards against the synthesis agents running against empty state, which
    yields a report full of "Data not available from research." while every
    agent-level contract still reports success.

    Args:
        state: ADK session state to inspect.
        minimum: Populated domains required to proceed. Defaults to
            MIN_DOMAIN_OUTPUTS_REQUIRED.
        fail_fast: When True the error is classed RESEARCH_DATA_MISSING, which
            maps to RETRY_SCOPE_NONE and aborts the job on the spot. When False
            it is classed MISSING_OUTPUT and the retry machinery re-runs
            ResearchSynthesizer first.
    """
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
        agent_name="ResearchSynthesizer",
        output_key="research_synthesizer_output",
        error_class="RESEARCH_DATA_MISSING" if fail_fast else "MISSING_OUTPUT",
    )


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
