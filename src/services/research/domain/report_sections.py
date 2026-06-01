"""Shared report section completeness helpers for guardrails and evaluation."""

from __future__ import annotations

import re

REPORT_SECTION_HEADERS: list[str] = [
    "Company Snapshot",
    "Company Overview",
    "Global Operations",
    "Key Executive",
    "Strategic Priorities",
    "Current Market Position",
    "Technology Landscape",
    "Regulatory",
    "Key Business",
    "Procurement",
    "Colt Technology Alignment",
    "Signals",
    "Source Summary",
]

TOTAL_REPORT_SECTIONS = 13


def _section_body(report: str, header_key: str) -> str:
    """Text from first header match until the next markdown ## heading."""
    report_lower = report.lower()
    idx = report_lower.find(header_key)
    if idx < 0:
        return ""
    body_start = idx + len(header_key)
    rest = report[body_start:]
    next_h = re.search(r"\n##\s", rest)
    end = body_start + next_h.start() if next_h else len(report)
    return report[body_start:end]


def count_populated_sections(report: str) -> int:
    """Count sections with header present and not a short 'publicly unavailable' stub."""
    report_lower = report.lower()
    populated = 0
    for header in REPORT_SECTION_HEADERS:
        key = header.lower()
        if key not in report_lower:
            continue
        body = _section_body(report, key).lower()
        if "publicly unavailable" in body and len(body.strip()) < 200:
            continue
        populated += 1
    return populated


def report_completeness_score(report: str) -> float:
    """Fraction of expected sections populated (0–1)."""
    return count_populated_sections(report) / TOTAL_REPORT_SECTIONS


_SECTION_13_REPLACE_RE = re.compile(
    r"##\s*13\.?\s*Source\s+Summary\b.*?(?=\n##[^#]|\Z)",
    re.IGNORECASE | re.DOTALL,
)

_SECTION_11_RE = re.compile(
    r"(##\s*11\.?\s*Strategic Opportunity[^\n]*\n)(.*?)(?=\n##[^#]|\Z)",
    re.IGNORECASE | re.DOTALL,
)

_FORBIDDEN_SECTION_11_CITATION_RE = re.compile(
    r"\s*\[Source:\s*[^\]]*(?:agent_output|google_search_agent)[^\]]*\]",
    re.IGNORECASE,
)


def replace_source_summary_section(report: str, section_md: str) -> str:
    """Replace or append Section 13 with a pre-built markdown block."""
    section_md = section_md.strip()
    if not section_md:
        return report
    if _SECTION_13_REPLACE_RE.search(report):
        return _SECTION_13_REPLACE_RE.sub(section_md, report, count=1)
    return report.rstrip() + "\n\n" + section_md + "\n"


def normalize_section_11_citations(report: str) -> str:
    """Remove internal output_key / tool-name citations from Section 11."""
    match = _SECTION_11_RE.search(report)
    if not match:
        return report
    header, body = match.group(1), match.group(2)
    cleaned_body = _FORBIDDEN_SECTION_11_CITATION_RE.sub("", body)
    return report[: match.start()] + header + cleaned_body + report[match.end() :]
