"""Signals leaves grouped under SignalsOrchestrator."""

from google.adk.agents import ParallelAgent

from ..prompts import (
    CAMPAIGN_SIGNALS_PROMPT,
    GROWTH_SIGNALS_PROMPT,
    RISK_SIGNALS_PROMPT,
)
from .specs import PlanAgentSpec, build_plan_react_agents


def create_signals_orchestrator():
    """Create SignalsOrchestrator with fresh PlanReAct signal agents."""
    specs = [
        PlanAgentSpec(
            name="GrowthSignals",
            prompt=GROWTH_SIGNALS_PROMPT,
            description="Finds hiring trends, M&A activity, and expansion signals.",
        ),
        PlanAgentSpec(
            name="RiskSignals",
            prompt=RISK_SIGNALS_PROMPT,
            description="Finds security incidents, regulatory challenges, and compliance signals.",
        ),
        PlanAgentSpec(
            name="CampaignSignals",
            prompt=CAMPAIGN_SIGNALS_PROMPT,
            description="Finds active campaigns, advertising trends, and brand positioning signals.",
        ),
    ]
    agents = build_plan_react_agents(specs)

    return ParallelAgent(
        name="SignalsOrchestrator",
        sub_agents=[
            agents["GrowthSignals"],
            agents["RiskSignals"],
            agents["CampaignSignals"],
        ],
    )
