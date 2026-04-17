"""
Synthesis Agents Module

Contains agents for analyzing alignment and compiling the final report.
"""

from ..prompts import (
    ALIGNMENT_PROMPT,
    REPORT_COMPILER_PROMPT,
    RESEARCH_VALIDATOR_PROMPT,
)
from ..utils.agent_factory import create_llm_agent


def create_research_validator():
    """Create a fresh ResearchValidator agent instance. Must be called per-run to avoid parent conflicts."""
    return create_llm_agent(
        name="ResearchValidator",
        instruction=RESEARCH_VALIDATOR_PROMPT,
        output_key="research_validation_output",
        description="Audits all research agent outputs for hallucinated (uncited) factual claims.",
    )


def create_synthesis_agents():
    """Create fresh synthesis agent instances. Must be called per-run to avoid parent conflicts."""
    alignment_analyst = create_llm_agent(
        name="AlignmentAnalyst",
        instruction=ALIGNMENT_PROMPT,
        output_key="alignment_output",
        description="Maps company challenges to Colt solutions.",
    )

    report_compiler = create_llm_agent(
        name="ReportCompiler",
        instruction=REPORT_COMPILER_PROMPT,
        output_key="final_report",
        description="Compiles the final markdown report from all research inputs.",
    )

    return alignment_analyst, report_compiler
