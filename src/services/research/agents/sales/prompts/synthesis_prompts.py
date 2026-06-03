"""Synthesis prompt compatibility re-exports."""

from .synthesis_alignment_prompts import ALIGNMENT_PROMPT
from .synthesis_context import COLT_DETAILS
from .synthesis_report_prompts import REPORT_COMPILER_PROMPT

__all__ = [
    "COLT_DETAILS",
    "ALIGNMENT_PROMPT",
    "REPORT_COMPILER_PROMPT",
]
