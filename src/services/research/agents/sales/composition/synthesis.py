"""Synthesis agents for alignment and report compilation."""

from ......core.config import settings
from ..prompts import ALIGNMENT_PROMPT, REPORT_COMPILER_PROMPT
from ..tools import validate_final_report_tool
from ..tools.alignment_context import make_alignment_context_tool
from .leaf import create_llm_agent, create_plan_react_agent


def create_synthesis_agents(company_name: str = "Unknown"):
    """Create fresh synthesis agent instances for each run."""
    # Create alignment context tool with company name
    alignment_context_tool = make_alignment_context_tool(company_name)

    alignment_analyst = create_llm_agent(
        name="AlignmentAnalyst",
        instruction=ALIGNMENT_PROMPT,
        output_key="alignment_output",
        description="Maps company challenges to Colt solutions using PDF context.",
        tools=[alignment_context_tool],
    )

    report_compiler = create_plan_react_agent(
        name="ReportCompiler",
        instruction=REPORT_COMPILER_PROMPT,
        output_key="final_report",
        description="Compiles the final markdown report from all research inputs.",
        include_web_search=False,
        include_bm25_verify=False,
        extra_tools=[validate_final_report_tool],
        model=settings.GEMINI_MODEL,
    )

    return alignment_analyst, report_compiler
