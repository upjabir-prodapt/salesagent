"""
Synthesis Agents Module

Contains agents for analyzing alignment and compiling the final report.
"""

from ......core.config import settings
from ..utils import create_plan_react_agent
from ..utils.tools import (
    colt_product_search_tool,
    make_report_verification_agent_tool,
)
from ..prompts import (
    ALIGNMENT_PROMPT,
    REPORT_COMPILER_PROMPT,
)


def create_synthesis_agents():
    """Create fresh synthesis agent instances. Must be called per-run to avoid parent conflicts."""
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
        extra_tools=[make_report_verification_agent_tool()],
        model=settings.EVALUATOR_MODEL,
    )

    return alignment_analyst, report_compiler
