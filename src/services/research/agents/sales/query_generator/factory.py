"""Factory for creating the query generator agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import json

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.tools.function_tool import FunctionTool

from ......core.config import settings
from ......core.logging_config import logger
from ......core.model import retry_config
from ...adk.retrying_llm_agent import RetryingLlmAgent
from ...adk.safety import get_safety_config_for_agent
from ...adk.callbacks import (
    after_agent_callback,
    after_model_callback,
    after_tool_callback,
    before_agent_callback,
    before_model_callback,
    before_tool_callback,
)
from ..callbacks.plan_react import (
    plan_after_agent,
    plan_after_model,
    plan_after_tool,
    plan_before_model,
    plan_before_tool,
)
from ..registry import AgentRegistry
from .bm25_selector import Bm25QuerySelector
from .prompt import build_query_generator_prompt
from .schemas import CandidateQueries, NormalizedQueryPlan, QueryWithMetadata


def _get_domain_list() -> list[str]:
    """Get all domain names from registry."""
    research_specs = AgentRegistry.research_specs()
    signal_specs = AgentRegistry.signal_specs()

    research_domains = [
        spec.name.replace("Agent", "").replace("Signals", "").lower()
        for spec in research_specs
    ]
    signal_domains = [
        spec.name.replace("Signals", "").lower() for spec in signal_specs
    ]

    return sorted(research_domains + signal_domains)


def _parse_candidate_queries(output: str) -> CandidateQueries | None:
    """Parse agent output to CandidateQueries."""
    try:
        # Try to extract JSON from output
        if "```json" in output:
            json_str = output.split("```json")[1].split("```")[0]
        elif "{" in output:
            # Find first { and last }
            start = output.find("{")
            end = output.rfind("}") + 1
            json_str = output[start:end]
        else:
            logger.warning("No JSON found in agent output")
            return None

        data = json.loads(json_str)
        candidates = CandidateQueries(**data)
        logger.info(
            f"Parsed {sum(len(q) for q in candidates.domain_queries.values())} candidate queries"
        )
        return candidates
    except Exception as e:
        logger.error(f"Failed to parse candidate queries: {e}")
        return None


def apply_bm25_selection(
    candidates: CandidateQueries, company_name: str
) -> NormalizedQueryPlan:
    """Apply BM25 selection to candidates."""
    selector = Bm25QuerySelector(company_name)
    flat_candidates = candidates.to_flat_list()
    plan = selector.select(flat_candidates)
    logger.info(
        f"BM25 selected {plan.budget_used} queries from {plan.total_candidates} candidates"
    )
    return plan


def make_query_generator_tool() -> FunctionTool:
    """Create tool for post-processing queries (parsing + BM25 selection)."""

    def process_queries(
        agent_output: str, company_name: str
    ) -> dict[str, Any]:
        """Process raw agent output into final query plan."""
        candidates = _parse_candidate_queries(agent_output)
        if not candidates:
            return {
                "status": "PARSE_FAILED",
                "error": "Could not parse agent output to CandidateQueries",
                "queries": [],
            }

        try:
            plan = apply_bm25_selection(candidates, company_name)
            return {
                "status": "SUCCESS",
                "queries": [
                    {
                        "query": q.query,
                        "domain": q.domain,
                        "year": q.year,
                    }
                    for q in plan.queries
                ],
                "stats": {
                    "total_candidates": plan.total_candidates,
                    "selected": plan.budget_used,
                    "per_domain": plan.per_domain_counts,
                },
            }
        except Exception as e:
            logger.error(f"BM25 selection failed: {e}")
            return {
                "status": "SELECTION_FAILED",
                "error": str(e),
                "queries": [],
            }

    return FunctionTool(process_queries)


class QueryGeneratorFactory:
    """Factory for creating the unified query generator agent."""

    @staticmethod
    def create_query_generator_agent(
        company_name: str,
        depth: str = "deep",
        current_year: int | None = None,
    ) -> LlmAgent:
        """Create a single unified query generator agent."""
        if current_year is None:
            current_year = datetime.now().year

        domains = _get_domain_list()
        prompt = build_query_generator_prompt(company_name, domains, current_year)

        # Add year to instruction
        instruction = f"""You are a Search Query Generation Specialist for {company_name}.
Current year: {current_year}
Depth level: {depth}

{prompt}
"""

        safety_config = get_safety_config_for_agent("QueryGeneratorAgent")

        agent = RetryingLlmAgent(
            name="QueryGeneratorAgent",
            model=Gemini(model=settings.GEMINI_MODEL, retry_options=retry_config),
            instruction=instruction,
            tools=[make_query_generator_tool()],
            output_key="query_generator_output",
            description="Generates search queries for all research domains",
            generate_content_config=safety_config,
            before_model_callback=before_model_callback,
            after_model_callback=after_model_callback,
            before_agent_callback=before_agent_callback,
            after_agent_callback=after_agent_callback,
            before_tool_callback=before_tool_callback,
            after_tool_callback=after_tool_callback,
        )

        logger.info(
            f"Created QueryGeneratorAgent for {company_name} (depth={depth}, year={current_year})"
        )
        return agent
