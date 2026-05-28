"""Research leaves grouped into sequential lanes for ResearchOrchestrator."""

from ....agents.factories import PlanReActAgentFactory


def create_research_agents():
    """Create research agents and sequential lanes for ResearchOrchestrator."""
    return PlanReActAgentFactory.build_research_lanes()
