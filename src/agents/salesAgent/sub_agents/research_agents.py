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
from .verifier_agents import create_verifier_agent


def create_research_agents():
    """Create fresh instances of all research agents with integrated verifiers."""
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
        "ExecutiveAgent",
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
        sub_agents=[
            firmographics_agent,
            create_verifier_agent("firmographics"),
            geographic_agent,
            create_verifier_agent("geographic"),
        ],
        description="Runs firmographics then geographic research with per-step verification.",
    )

    strategy_compliance_agent = SequentialAgent(
        name="StrategyComplianceAgent",
        sub_agents=[
            strategy_agent,
            create_verifier_agent("strategy"),
            compliance_agent,
            create_verifier_agent("compliance"),
        ],
        description="Runs strategy then compliance research with per-step verification.",
    )

    market_ecosystem_agent = SequentialAgent(
        name="MarketEcosystemAgent",
        sub_agents=[
            market_agent,
            create_verifier_agent("market"),
            ecosystem_agent,
            create_verifier_agent("ecosystem"),
            procurement_agent,
            create_verifier_agent("procurement"),
        ],
        description="Runs market, ecosystem, and procurement research with per-step verification.",
    )

    tech_stack_pipeline = SequentialAgent(
        name="TechStackPipeline",
        sub_agents=[
            tech_stack_agent,
            create_verifier_agent("tech_stack"),
        ],
        description="Runs tech stack research with verification.",
    )

    executive_pipeline = SequentialAgent(
        name="ExecutivePipeline",
        sub_agents=[
            executive_agent,
            create_verifier_agent("executive"),
        ],
        description="Identifies leadership team with verification.",
    )

    return (
        firmographics_geographic_agent,
        executive_pipeline,
        strategy_compliance_agent,
        market_ecosystem_agent,
        tech_stack_pipeline,
    )
