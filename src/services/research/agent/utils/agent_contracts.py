"""Stable agent output-key contracts and validators."""

from __future__ import annotations

from typing import Any

from .....core.exceptions import AgentOutputError

AGENT_OUTPUT_KEYS: dict[str, str] = {
    "FirmographicsAgent": "firmographicsagent_output",
    "GeographicAgent": "geographicagent_output",
    "ExecutiveAgent": "executiveagent_output",
    "StrategyAgent": "strategyagent_output",
    "ComplianceAgent": "complianceagent_output",
    "MarketAgent": "marketagent_output",
    "EcosystemAgent": "ecosystemagent_output",
    "TechStackAgent": "techstackagent_output",
    "ProcurementAgent": "procurementagent_output",
    "GrowthSignals": "growthsignals_output",
    "RiskSignals": "risksignals_output",
    "CampaignSignals": "campaignsignals_output",
    "AlignmentAnalyst": "alignment_output",
    "ReportCompiler": "final_report",
}


def get_output_key(agent_name: str) -> str | None:
    return AGENT_OUTPUT_KEYS.get(agent_name)


def is_tracked_agent(agent_name: str) -> bool:
    return agent_name in AGENT_OUTPUT_KEYS


def validate_agent_output(state: dict[str, Any], agent_name: str) -> None:
    """Raise AgentOutputError if a tracked agent has no non-empty output in state."""
    output_key = get_output_key(agent_name)
    if not output_key:
        return
    value = state.get(output_key)
    if value is None or not str(value).strip():
        raise AgentOutputError(
            (
                f"{agent_name} completed but required output '{output_key}' remained empty "
                "in session state after model-response output persistence."
            ),
            agent_name=agent_name,
            output_key=output_key,
            error_class="MISSING_OUTPUT",
        )

