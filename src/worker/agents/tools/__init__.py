"""Callable ADK tools for sales research agents."""

from .domain_outputs import (
    SAVE_DOMAIN_OUTPUT_TOOL,
    extract_domain_payloads,
    missing_domain_keys,
    recover_domain_outputs,
    save_domain_output,
    save_domain_output_tool,
)
from .embedding_similarity import compute_semantic_groundedness
from .report_validation import (
    VALIDATE_FINAL_REPORT_TOOL,
    aggregate_raw_search_cache,
    ensure_report_validated,
    persist_report_validation_state,
    run_report_output_guardrail,
    validate_final_report,
    validate_final_report_tool,
)
from .search import (
    SEARCH_TOOL_NAME,
    google_search,
    verify_draft_answer,
    verify_draft_answer_tool,
)


def create_llm_agent(*args, **kwargs):
    """Compatibility proxy — use graph.sales.composition.leaf."""
    from ..composition.leaf import create_llm_agent as _create_llm_agent

    return _create_llm_agent(*args, **kwargs)


__all__ = [
    "create_llm_agent",
    "SAVE_DOMAIN_OUTPUT_TOOL",
    "SEARCH_TOOL_NAME",
    "VALIDATE_FINAL_REPORT_TOOL",
    "aggregate_raw_search_cache",
    "compute_semantic_groundedness",
    "ensure_report_validated",
    "extract_domain_payloads",
    "google_search",
    "missing_domain_keys",
    "persist_report_validation_state",
    "recover_domain_outputs",
    "run_report_output_guardrail",
    "save_domain_output",
    "save_domain_output_tool",
    "validate_final_report",
    "validate_final_report_tool",
    "verify_draft_answer",
    "verify_draft_answer_tool",
]
