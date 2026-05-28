"""Research leaves grouped into sequential lanes for ResearchOrchestrator."""

from google.adk.agents import SequentialAgent

from ..prompts import (
    COMPLIANCE_PROMPT,
    ECOSYSTEM_PROMPT,
    EXECUTIVE_PROMPT,
    FIRMOGRAPHICS_PROMPT,
    GEOGRAPHIC_PROMPT,
    MARKET_PROMPT,
    PROCUREMENT_PROMPT,
    STRATEGY_PROMPT,
    TECH_STACK_PROMPT,
)
from .specs import PlanAgentSpec, build_plan_react_agents


_AGENT_CONFIGS = [
    PlanAgentSpec(
        "FirmographicsAgent",
        FIRMOGRAPHICS_PROMPT,
        "Researches company snapshot including revenue, employees, market cap, ownership structure.",
    ),
    PlanAgentSpec(
        "GeographicAgent",
        GEOGRAPHIC_PROMPT,
        "Maps global operations, office locations, data centers, and regional revenue distribution.",
    ),
    PlanAgentSpec(
        "ExecutiveAgent",
        EXECUTIVE_PROMPT,
        "Identifies leadership team, board members, key influencers with detailed profiles.",
    ),
    PlanAgentSpec(
        "StrategyAgent",
        STRATEGY_PROMPT,
        "Analyzes strategic priorities, M&A strategy, competitive advantages, and key challenges.",
    ),
    PlanAgentSpec(
        "ComplianceAgent",
        COMPLIANCE_PROMPT,
        "Identifies regulations, certifications, audit history, and compliance issues.",
    ),
    PlanAgentSpec(
        "MarketAgent",
        MARKET_PROMPT,
        "Analyzes market position, revenue breakdown, competitors, and commercial leverage points.",
    ),
    PlanAgentSpec(
        "EcosystemAgent",
        ECOSYSTEM_PROMPT,
        "Maps partnerships, strategic alliances, Colt dependencies, and co-innovation potential.",
    ),
    PlanAgentSpec(
        "TechStackAgent",
        TECH_STACK_PROMPT,
        "Profiles technology landscape, cloud strategy, infrastructure models, and digital investments.",
    ),
    PlanAgentSpec(
        "ProcurementAgent",
        PROCUREMENT_PROMPT,
        "Analyzes procurement patterns, contract cycles, RFP activity, and vendor reviews.",
    ),
]


def create_research_agents():
    """Create research agents and sequential lanes for ResearchOrchestrator."""
    agents = build_plan_react_agents(_AGENT_CONFIGS)

    firmographics_geographic_agent = SequentialAgent(
        name="FirmographicsGeographicAgent",
        sub_agents=[agents["FirmographicsAgent"], agents["GeographicAgent"]],
        description="Runs firmographics then geographic research.",
    )

    strategy_compliance_agent = SequentialAgent(
        name="StrategyComplianceAgent",
        sub_agents=[agents["StrategyAgent"], agents["ComplianceAgent"]],
        description="Runs strategy then compliance research.",
    )

    market_ecosystem_agent = SequentialAgent(
        name="MarketEcosystemAgent",
        sub_agents=[
            agents["MarketAgent"],
            agents["EcosystemAgent"],
            agents["ProcurementAgent"],
        ],
        description="Runs market, ecosystem, and procurement research.",
    )

    return (
        firmographics_geographic_agent,
        agents["ExecutiveAgent"],
        strategy_compliance_agent,
        market_ecosystem_agent,
        agents["TechStackAgent"],
    )
