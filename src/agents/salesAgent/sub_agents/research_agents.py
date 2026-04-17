"""
Research Agents Module

Contains specialized LLM agents for researching different aspects of a company.
"""

from google.adk.agents import SequentialAgent
from google.adk.tools import google_search

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


def create_research_agents():
    """Create fresh instances of all research agents. Must be called per-run to avoid parent conflicts."""
    firmographics_agent = create_llm_agent(
        "FirmographicsAgent",
        FIRMOGRAPHICS_PROMPT,
        "Researches company snapshot including revenue, employees, market cap, ownership structure.",
        tools=[google_search],
    )

    geographic_agent = create_llm_agent(
        "GeographicAgent",
        GEOGRAPHIC_PROMPT,
        "Maps global operations, office locations, data centers, and regional revenue distribution.",
        tools=[google_search],
    )

    executive_agent = create_llm_agent(
        "ExecutivePipeline",
        EXECUTIVE_PROMPT,
        "Identifies leadership team, board members, key influencers with detailed profiles.",
        tools=[google_search],
    )

    strategy_agent = create_llm_agent(
        "StrategyAgent",
        STRATEGY_PROMPT,
        "Analyzes strategic priorities, M&A strategy, competitive advantages, and key challenges.",
        tools=[google_search],
    )

    compliance_agent = create_llm_agent(
        "ComplianceAgent",
        COMPLIANCE_PROMPT,
        "Identifies regulations, certifications, audit history, and compliance issues.",
        tools=[google_search],
    )

    market_agent = create_llm_agent(
        "MarketAgent",
        MARKET_PROMPT,
        "Analyzes market position, revenue breakdown, competitors, and commercial leverage points.",
        tools=[google_search],
    )

    ecosystem_agent = create_llm_agent(
        "EcosystemAgent",
        ECOSYSTEM_PROMPT,
        "Maps partnerships, strategic alliances, Colt dependencies, and co-innovation potential.",
        tools=[google_search],
    )

    tech_stack_agent = create_llm_agent(
        "TechStackAgent",
        TECH_STACK_PROMPT,
        "Profiles technology landscape, cloud strategy, infrastructure models, and digital investments.",
        tools=[google_search],
    )

    procurement_agent = create_llm_agent(
        "ProcurementAgent",
        PROCUREMENT_PROMPT,
        "Analyzes procurement patterns, contract cycles, RFP activity, and vendor reviews.",
        tools=[google_search],
    )

    firmographics_geographic_agent = SequentialAgent(
        name="FirmographicsGeographicAgent",
        sub_agents=[firmographics_agent, geographic_agent],
        description="Runs firmographics then geographic research sequentially, sharing session context.",
    )

    strategy_compliance_agent = SequentialAgent(
        name="StrategyComplianceAgent",
        sub_agents=[strategy_agent, compliance_agent],
        description="Runs strategy then compliance research sequentially, sharing session context.",
    )

    market_ecosystem_agent = SequentialAgent(
        name="MarketEcosystemAgent",
        sub_agents=[market_agent, ecosystem_agent, procurement_agent],
        description="Runs market, ecosystem, and procurement research sequentially (covers Sections 4, 4.1, 6.1, 7, 9).",
    )

    return (
        firmographics_geographic_agent,
        executive_agent,
        strategy_compliance_agent,
        market_ecosystem_agent,
        tech_stack_agent,
    )
