"""Research lanes grouped for ResearchOrchestrator."""

from ..factory import PlanReActAgentFactory


def create_research_agents():
    """Create research agents and sequential lanes for ResearchOrchestrator."""
    return PlanReActAgentFactory.build_research_lanes()
