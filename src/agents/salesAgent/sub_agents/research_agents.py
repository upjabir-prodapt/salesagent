"""
Research Agents Module

Contains specialized LLM agents for researching different aspects of a company.
"""

from dataclasses import dataclass

from google.adk.agents import SequentialAgent
from google.adk.planners import PlanReActPlanner
from google.adk.tools import google_search

from ....core.config import settings
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
from ..utils.agent_factory import create_llm_agent


@dataclass
class AgentConfig:
    name: str
    prompt: str
    description: str


_AGENT_CONFIGS = [
    AgentConfig(
        "FirmographicsAgent",
        FIRMOGRAPHICS_PROMPT,
        "Researches company snapshot including revenue, employees, market cap, ownership structure.",
    ),
    AgentConfig(
        "GeographicAgent",
        GEOGRAPHIC_PROMPT,
        "Maps global operations, office locations, data centers, and regional revenue distribution.",
    ),
    AgentConfig(
        "ExecutiveAgent",
        EXECUTIVE_PROMPT,
        "Identifies leadership team, board members, key influencers with detailed profiles.",
    ),
    AgentConfig(
        "StrategyAgent",
        STRATEGY_PROMPT,
        "Analyzes strategic priorities, M&A strategy, competitive advantages, and key challenges.",
    ),
    AgentConfig(
        "ComplianceAgent",
        COMPLIANCE_PROMPT,
        "Identifies regulations, certifications, audit history, and compliance issues.",
    ),
    AgentConfig(
        "MarketAgent",
        MARKET_PROMPT,
        "Analyzes market position, revenue breakdown, competitors, and commercial leverage points.",
    ),
    AgentConfig(
        "EcosystemAgent",
        ECOSYSTEM_PROMPT,
        "Maps partnerships, strategic alliances, Colt dependencies, and co-innovation potential.",
    ),
    AgentConfig(
        "TechStackAgent",
        TECH_STACK_PROMPT,
        "Profiles technology landscape, cloud strategy, infrastructure models, and digital investments.",
    ),
    AgentConfig(
        "ProcurementAgent",
        PROCUREMENT_PROMPT,
        "Analyzes procurement patterns, contract cycles, RFP activity, and vendor reviews.",
    ),
]


def _planner():
    """Create planner instance per agent when enabled."""
    return PlanReActPlanner() if settings.USE_PLAN_REACT_PLANNER else None


def create_research_agents():
    """Create fresh instances of all research agents."""
    agents = {}
    for config in _AGENT_CONFIGS:
        agents[config.name] = create_llm_agent(
            config.name,
            config.prompt,
            config.description,
            tools=[google_search],
            planner=_planner(),
        )

    firmographics_geographic_agent = SequentialAgent(
        name="FirmographicsGeographicAgent",
        sub_agents=[
            agents["FirmographicsAgent"],
            agents["GeographicAgent"],
        ],
        description="Runs firmographics then geographic research.",
    )

    strategy_compliance_agent = SequentialAgent(
        name="StrategyComplianceAgent",
        sub_agents=[
            agents["StrategyAgent"],
            agents["ComplianceAgent"],
        ],
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

    tech_stack_pipeline = SequentialAgent(
        name="TechStackPipeline",
        sub_agents=[
            agents["TechStackAgent"],
        ],
        description="Runs tech stack research.",
    )

    executive_pipeline = SequentialAgent(
        name="ExecutivePipeline",
        sub_agents=[
            agents["ExecutiveAgent"],
        ],
        description="Identifies leadership team.",
    )

    return (
        firmographics_geographic_agent,
        executive_pipeline,
        strategy_compliance_agent,
        market_ecosystem_agent,
        tech_stack_pipeline,
    )
