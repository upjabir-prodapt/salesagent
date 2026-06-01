"""Factories for building sales research ADK agents from registry specs."""

from __future__ import annotations

from google.adk.agents import ParallelAgent, SequentialAgent

from .registry import AgentRegistry, PlanAgentSpec
from .tools import create_plan_react_agent


class PlanReActAgentFactory:
    """Factory methods for PlanReAct leaf and composite agents."""

    @staticmethod
    def build_agents(specs: tuple[PlanAgentSpec, ...]) -> dict[str, object]:
        return {
            spec.name: create_plan_react_agent(spec.name, spec.prompt, spec.description)
            for spec in specs
        }

    @classmethod
    def build_research_lanes(
        cls,
    ) -> tuple[SequentialAgent, object, SequentialAgent, SequentialAgent, object]:
        agents = cls.build_agents(AgentRegistry.research_specs())

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

    @classmethod
    def build_signals_orchestrator(cls) -> ParallelAgent:
        agents = cls.build_agents(AgentRegistry.signal_specs())
        return ParallelAgent(
            name="SignalsOrchestrator",
            sub_agents=[
                agents["GrowthSignals"],
                agents["RiskSignals"],
                agents["CampaignSignals"],
            ],
        )

    @staticmethod
    def build_synthesis_agents():
        from .sub_agents.synthesis import create_synthesis_agents

        return create_synthesis_agents()
