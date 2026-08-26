"""ReportCompiler plain LLM agent factory."""

from .leaf import create_llm_agent
from .prompts import REPORT_COMPILER_PROMPT
from .tools.report_validation import validate_final_report_tool


def create_compiler_agent(company_name: str = "Unknown"):
    """Create the ReportCompiler agent without PlanReAct."""
    return create_llm_agent(
        name="ReportCompiler",
        instruction=REPORT_COMPILER_PROMPT,
        output_key="final_report",
        include_contents="none",
        description="Compiles the final markdown report from all research inputs.",
        tools=[validate_final_report_tool],
    )


__all__ = ["create_compiler_agent"]
