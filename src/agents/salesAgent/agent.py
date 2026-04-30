"""
SalesAgent Main Module

Defines the main agent orchestration structure for lead generation research.
"""

from google.adk.agents import ParallelAgent, SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App, ResumabilityConfig
from google.adk.plugins import ReflectAndRetryToolPlugin
from loguru import logger

from .sub_agents.research_agents import create_research_agents
from .sub_agents.signals_agent import create_signals_orchestrator
from .sub_agents.synthesis_agents import (
    create_synthesis_agents,
)


def create_sales_agent_app():
    logger.info("Building agent orchestration structure...")

    # Create fresh agent instances each call to avoid parent-assignment conflicts on retry
    (
        firmographics_geographic_agent,
        executive_agent,
        strategy_compliance_agent,
        market_ecosystem_agent,
        tech_stack_agent,
    ) = create_research_agents()
    signals_orchestrator = create_signals_orchestrator()
    alignment_analyst, report_compiler = create_synthesis_agents()

    # 1. The Massive Parallel Research Orchestrator
    logger.info("Creating ResearchOrchestrator (6 parallel sub-agents)...")
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
    logger.debug(
        f"ResearchOrchestrator has {len(research_orchestrator.sub_agents)} sub-agents"
    )

    # 2. The Main Sequential Flow
    logger.info("Creating SalesResearchAgent (sequential pipeline)...")
    sales_research_agent = SequentialAgent(
        name="SalesResearchAgent",
        sub_agents=[
            research_orchestrator,
            alignment_analyst,
            report_compiler,
        ],
        description="An agent that performs deep sales research on a company and generates a strategic lead report.",
    )
    logger.debug(
        "SalesResearchAgent pipeline: ResearchOrchestrator -> AlignmentAnalyst -> ReportCompiler"
    )

    logger.success("SalesResearchAgent fully initialized and ready")

    app = App(
        name="sales_research_app",
        root_agent=sales_research_agent,
        resumability_config=ResumabilityConfig(is_resumable=True),
        context_cache_config=ContextCacheConfig(ttl_seconds=3600),
        plugins=[ReflectAndRetryToolPlugin()],
    )
    return app
