"""ADK agents and master workflow for sales research."""

from .alignment_agent import create_alignment_agent
from .compiler_agent import create_compiler_agent
from .keyword_agent import QueryGeneratorFactory
from .prompts import ALIGNMENT_PROMPT, REPORT_COMPILER_PROMPT
from .search_agent import ParallelSearchAgent
from .workflow import SalesAgentAppFactory, SalesResearchWorkflowAgent


def create_sales_agent_app(company_name: str = "Unknown"):
    """Build the ADK app through the centralized application factory."""
    return SalesAgentAppFactory().create(company_name)


__all__ = [
    "SalesAgentAppFactory",
    "SalesResearchWorkflowAgent",
    "ParallelSearchAgent",
    "QueryGeneratorFactory",
    "create_alignment_agent",
    "create_compiler_agent",
    "create_sales_agent_app",
    "ALIGNMENT_PROMPT",
    "REPORT_COMPILER_PROMPT",
]
