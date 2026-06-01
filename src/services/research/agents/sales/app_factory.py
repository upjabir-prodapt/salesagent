"""Application factory for sales research ADK app composition."""

from __future__ import annotations

from google.adk.agents import ParallelAgent, SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App, ResumabilityConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini
from google.adk.plugins import ReflectAndRetryToolPlugin

from .....core.config import settings
from .....core.logging_config import logger
from .....core.model import retry_config
from .factory import PlanReActAgentFactory


class SalesAgentAppFactory:
    """Build the full ADK application graph for a research job run."""

    def create(self) -> App:
        logger.info("Building agent orchestration structure...")

        (
            firmographics_geographic_agent,
            executive_agent,
            strategy_compliance_agent,
            market_ecosystem_agent,
            tech_stack_agent,
        ) = PlanReActAgentFactory.build_research_lanes()
        signals_orchestrator = PlanReActAgentFactory.build_signals_orchestrator()
        alignment_analyst, report_compiler = PlanReActAgentFactory.build_synthesis_agents()

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
        logger.info("SalesResearchAgent fully initialized and ready")

        compaction_config = None
        if settings.AGENT_EVENTS_COMPACT_ENABLED:
            summarizer = LlmEventSummarizer(
                llm=Gemini(
                    model=settings.AGENT_COMPACT_SUMMARIZER_MODEL,
                    retry_options=retry_config,
                )
            )
            compaction_config = EventsCompactionConfig(
                compaction_interval=settings.AGENT_EVENTS_COMPACT_INTERVAL,
                overlap_size=settings.AGENT_EVENTS_COMPACT_OVERLAP,
                token_threshold=settings.AGENT_EVENTS_COMPACT_TOKEN_THRESHOLD,
                event_retention_size=settings.AGENT_EVENTS_COMPACT_RETENTION,
                summarizer=summarizer,
            )

        return App(
            name="sales_research_app",
            root_agent=sales_research_agent,
            resumability_config=ResumabilityConfig(is_resumable=True),
            context_cache_config=ContextCacheConfig(ttl_seconds=3600),
            events_compaction_config=compaction_config,
            plugins=[ReflectAndRetryToolPlugin(max_retries=3)],
        )
