"""AlignmentAnalyst agent factory."""

from src.worker.domain.schemas import ColtAlignmentOutput

from .leaf import create_llm_agent
from .prompts import ALIGNMENT_PROMPT
from .tools.alignment_context import make_alignment_context_tool


def create_alignment_agent(company_name: str = "Unknown"):
    """Create the AlignmentAnalyst agent with context-cached catalog loading."""
    alignment_context_tool = make_alignment_context_tool(company_name)
    return create_llm_agent(
        name="AlignmentAnalyst",
        instruction=ALIGNMENT_PROMPT,
        output_key="alignment_output",
        output_schema=ColtAlignmentOutput,
        include_contents="none",
        description="Maps company challenges to Colt solutions using PDF context.",
        tools=[alignment_context_tool],
    )


__all__ = ["create_alignment_agent"]
