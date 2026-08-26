"""Quality evaluation service and metrics package."""

from .config import DIMENSION_CONFIG, RESEARCH_AGENT_OUTPUT_KEYS
from .section_a import empty_section_a, parse_and_score_section_a
from .section_b import (
    build_section_b_result,
    compute_agent_output_coverage,
    compute_completeness,
    compute_domain_groundedness,
    compute_evidence_breadth,
    compute_groundedness,
)
from .service import EvaluationService

__all__ = [
    "EvaluationService",
    "DIMENSION_CONFIG",
    "RESEARCH_AGENT_OUTPUT_KEYS",
    "parse_and_score_section_a",
    "empty_section_a",
    "compute_agent_output_coverage",
    "compute_completeness",
    "compute_evidence_breadth",
    "compute_groundedness",
    "compute_domain_groundedness",
    "build_section_b_result",
]
