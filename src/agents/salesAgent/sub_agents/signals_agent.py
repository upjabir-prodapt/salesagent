"""
Signals Agents Module

Contains agents for detecting growth, risk, and campaign signals for a company.
"""

from google.adk.agents import ParallelAgent
from google.adk.tools import google_search

from ..prompts import (
    CAMPAIGN_SIGNALS_PROMPT,
    GROWTH_SIGNALS_PROMPT,
    RISK_SIGNALS_PROMPT,
)
from ..utils.agent_factory import create_llm_agent


def create_signals_orchestrator():
    """Create a fresh SignalsOrchestrator instance. Must be called per-run to avoid parent conflicts."""
    growth_signals_agent = create_llm_agent(
        name="GrowthSignals",
        instruction=GROWTH_SIGNALS_PROMPT,
        description="Finds hiring trends, M&A activity, and expansion signals.",
        tools=[google_search],
    )

    risk_signals_agent = create_llm_agent(
        name="RiskSignals",
        instruction=RISK_SIGNALS_PROMPT,
        description="Finds security incidents, regulatory challenges, and compliance signals.",
        tools=[google_search],
    )

    campaign_signals_agent = create_llm_agent(
        name="CampaignSignals",
        instruction=CAMPAIGN_SIGNALS_PROMPT,
        description="Finds active campaigns, advertising trends, and brand positioning signals.",
        tools=[google_search],
    )

    return ParallelAgent(
        name="SignalsOrchestrator",
        sub_agents=[growth_signals_agent, risk_signals_agent, campaign_signals_agent],
    )
