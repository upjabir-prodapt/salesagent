"""Synthesis agents for alignment and report compilation."""

from ......core.config import settings
from ..prompts import ALIGNMENT_PROMPT, REPORT_COMPILER_PROMPT
from ..tools import colt_product_search_tool, validate_final_report_tool
from .leaf import create_plan_react_agent


def create_synthesis_agents():
    """Create fresh synthesis agent instances for each run."""
    alignment_analyst = create_plan_react_agent(
        name="AlignmentAnalyst",
        instruction=ALIGNMENT_PROMPT,
        output_key="alignment_output",
        description="Maps company challenges to Colt solutions.",
        extra_tools=[colt_product_search_tool],
        model=settings.EVALUATOR_MODEL,
    )

    report_compiler = create_plan_react_agent(
        name="ReportCompiler",
        instruction=REPORT_COMPILER_PROMPT,
        output_key="final_report",
        description="Compiles the final markdown report from all research inputs.",
        include_web_search=False,
        include_bm25_verify=False,
        extra_tools=[validate_final_report_tool],
        model=settings.EVALUATOR_MODEL,
    )

    return alignment_analyst, report_compiler
