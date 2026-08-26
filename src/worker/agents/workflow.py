"""Master SalesResearchWorkflowAgent and SalesAgentAppFactory."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App, ResumabilityConfig
from google.adk.apps.app import EventsCompactionConfig
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.events import Event
from google.adk.models import Gemini
from google.adk.plugins import ReflectAndRetryToolPlugin
from google.adk.utils.context_utils import Aclosing

from src.shared.config import settings
from src.shared.logging_config import logger
from src.worker.model import retry_config

from .alignment_agent import create_alignment_agent
from .compiler_agent import create_compiler_agent
from .keyword_agent import QueryGeneratorFactory
from .search_agent import ParallelSearchAgent


class SalesResearchWorkflowAgent(BaseAgent):
    """Custom workflow agent that orchestrates the 4-phase research pipeline."""

    name: str = "SalesResearchAgent"
    description: str = (
        "Orchestrates keyword generation, parallel cached web search, "
        "Colt portfolio alignment, and final report compilation."
    )

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Execute each sub-agent in sequence with clean context isolation."""
        logger.info("[Workflow] Starting SalesResearchWorkflowAgent execution")

        for sub_agent in self.sub_agents:
            logger.info(f"[Workflow] Dispatching sub-agent: {sub_agent.name}")
            async with Aclosing(sub_agent.run_async(ctx)) as agen:
                async for event in agen:
                    yield event
                    if ctx.should_pause_invocation(event):
                        logger.info(f"[Workflow] Invocation paused at {sub_agent.name}")
                        return

        logger.info("[Workflow] SalesResearchWorkflowAgent completed successfully")


class SalesAgentAppFactory:
    """Build the full ADK application graph for a research job run."""

    def create(self, company_name: str = "Unknown") -> App:
        logger.info(f"Building ADK graph for company: {company_name}")

        query_generator = QueryGeneratorFactory.create_query_generator_agent(
            company_name
        )
        parallel_search = ParallelSearchAgent()
        alignment_analyst = create_alignment_agent(company_name)
        report_compiler = create_compiler_agent(company_name)

        sales_research_agent = SalesResearchWorkflowAgent(
            name="SalesResearchAgent",
            sub_agents=[
                query_generator,
                parallel_search,
                alignment_analyst,
                report_compiler,
            ],
            description="Orchestrates 4-phase sales research swarm.",
        )

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


__all__ = [
    "SalesResearchWorkflowAgent",
    "SalesAgentAppFactory",
]
