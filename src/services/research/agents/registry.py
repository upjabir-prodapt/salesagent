"""Central registry of sales research agent specifications."""

from __future__ import annotations

from dataclasses import dataclass

from ..agent.sales.prompts import (
    CAMPAIGN_SIGNALS_PROMPT,
    COMPLIANCE_PROMPT,
    ECOSYSTEM_PROMPT,
    EXECUTIVE_PROMPT,
    FIRMOGRAPHICS_PROMPT,
    GEOGRAPHIC_PROMPT,
    GROWTH_SIGNALS_PROMPT,
    MARKET_PROMPT,
    PROCUREMENT_PROMPT,
    RISK_SIGNALS_PROMPT,
    STRATEGY_PROMPT,
    TECH_STACK_PROMPT,
)


@dataclass(frozen=True)
class PlanAgentSpec:
    """Declarative PlanReAct leaf agent specification."""

    name: str
    prompt: str
    description: str


class AgentRegistry:
    """Source of truth for research/signal leaf-agent definitions."""

    @staticmethod
    def research_specs() -> tuple[PlanAgentSpec, ...]:
        return (
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
        )

    @staticmethod
    def signal_specs() -> tuple[PlanAgentSpec, ...]:
        return (
            PlanAgentSpec(
                "GrowthSignals",
                GROWTH_SIGNALS_PROMPT,
                "Finds hiring trends, M&A activity, and expansion signals.",
            ),
            PlanAgentSpec(
                "RiskSignals",
                RISK_SIGNALS_PROMPT,
                "Finds security incidents, regulatory challenges, and compliance signals.",
            ),
            PlanAgentSpec(
                "CampaignSignals",
                CAMPAIGN_SIGNALS_PROMPT,
                "Finds active campaigns, advertising trends, and brand positioning signals.",
            ),
        )
