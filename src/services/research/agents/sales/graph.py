"""Helpers for constructing the top-level sales research graph."""

from __future__ import annotations

from google.adk.agents import ParallelAgent, SequentialAgent

from .factory import PlanReActAgentFactory


def build_sales_research_graph() -> SequentialAgent:
    """Build and return the SalesResearchAgent sequential graph."""
    (
        firmographics_geographic_agent,
        executive_agent,
        strategy_compliance_agent,
        market_ecosystem_agent,
        tech_stack_agent,
    ) = PlanReActAgentFactory.build_research_lanes()
    signals_orchestrator = PlanReActAgentFactory.build_signals_orchestrator()
    alignment_analyst, report_compiler = PlanReActAgentFactory.build_synthesis_agents()

    research_orchestrator = ParallelAgent(
        name="ResearchOrchestrator",
        sub_agents=[
            firmographics_geographic_agent,
            executive_agent,
            strategy_compliance_agent,
            market_ecosystem_agent,
            tech_stack_agent,
            signals_orchestrator,
        ],
    )
    return SequentialAgent(
        name="SalesResearchAgent",
        sub_agents=[research_orchestrator, alignment_analyst, report_compiler],
        description="An agent that performs deep sales research on a company and generates a strategic lead report.",
    )
