"""
Signals Agents Module

Three PlanReAct agents run in parallel under SignalsOrchestrator.
"""

from google.adk.agents import ParallelAgent

from ..utils import create_plan_react_agent
from ..prompts import (
    CAMPAIGN_SIGNALS_PROMPT,
    GROWTH_SIGNALS_PROMPT,
    RISK_SIGNALS_PROMPT,
)


def create_signals_orchestrator():
    """Create SignalsOrchestrator with fresh PlanReAct agents (one GoogleSearchAgentTool each)."""
    growth_signals_agent = create_plan_react_agent(
        name="GrowthSignals",
        instruction=GROWTH_SIGNALS_PROMPT,
        description="Finds hiring trends, M&A activity, and expansion signals.",
    )

    risk_signals_agent = create_plan_react_agent(
        name="RiskSignals",
        instruction=RISK_SIGNALS_PROMPT,
        description="Finds security incidents, regulatory challenges, and compliance signals.",
    )

    campaign_signals_agent = create_plan_react_agent(
        name="CampaignSignals",
        instruction=CAMPAIGN_SIGNALS_PROMPT,
        description="Finds active campaigns, advertising trends, and brand positioning signals.",
    )

    return ParallelAgent(
        name="SignalsOrchestrator",
        sub_agents=[growth_signals_agent, risk_signals_agent, campaign_signals_agent],
    )
