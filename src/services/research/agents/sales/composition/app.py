"""Application factory for sales research ADK app composition."""

from __future__ import annotations

from google.adk.agents import SequentialAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App, ResumabilityConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.models import Gemini
from google.adk.plugins import ReflectAndRetryToolPlugin

from ......core.config import settings
from ......core.logging_config import logger
from ......core.model import retry_config
from .lanes import PlanReActAgentFactory
from ..query_generator import QueryGeneratorFactory


class SalesAgentAppFactory:
    """Build the full ADK application graph for a research job run."""

    def create(self, company_name: str = "Unknown") -> App:
        logger.info("Building agent orchestration structure...")

        # Create unified query generator agent
        query_generator = QueryGeneratorFactory.create_query_generator_agent(company_name)

        # Create synthesis agents (pass company_name for context tools)
        alignment_analyst, report_compiler = (
            PlanReActAgentFactory.build_synthesis_agents(company_name)
        )

        logger.info("Creating SalesResearchAgent (sequential pipeline)...")
        sales_research_agent = SequentialAgent(
            name="SalesResearchAgent",
            sub_agents=[
                query_generator,
                alignment_analyst,
                report_compiler,
            ],
            description="An agent that generates research queries, performs alignment analysis, and generates a strategic lead report.",
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
