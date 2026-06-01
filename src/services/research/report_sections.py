"""Backward-compatible imports for report section helpers."""

from .domain.report_sections import (
    REPORT_SECTION_HEADERS,
    TOTAL_REPORT_SECTIONS,
    count_populated_sections,
    normalize_section_11_citations,
    replace_source_summary_section,
    report_completeness_score,
)

__all__ = [
    "REPORT_SECTION_HEADERS",
    "TOTAL_REPORT_SECTIONS",
    "count_populated_sections",
    "normalize_section_11_citations",
    "replace_source_summary_section",
    "report_completeness_score",
]
