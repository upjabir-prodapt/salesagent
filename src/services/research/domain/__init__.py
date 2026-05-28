"""Domain contracts and models for research services."""

from .agent_contracts import (
    AGENT_CONTRACTS,
    AGENT_OUTPUT_KEYS,
    AgentContract,
    get_agent_contract,
    get_output_key,
    is_tracked_agent,
)
from .models import EvidenceRecord, ResearchJob, ResearchMetrics
from .session_state import ResearchSessionState

__all__ = [
    "AgentContract",
    "AGENT_CONTRACTS",
    "AGENT_OUTPUT_KEYS",
    "get_agent_contract",
    "get_output_key",
    "is_tracked_agent",
    "ResearchJob",
    "ResearchMetrics",
    "EvidenceRecord",
    "ResearchSessionState",
]
