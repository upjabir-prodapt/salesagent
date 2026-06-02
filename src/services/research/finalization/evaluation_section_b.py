"""Section B automated metric helpers."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ....core.config import settings
from ....core.logging_config import logger
from .evaluation_config import (
    EXPECTED_SECTION_COUNT,
    MIN_EXPECTED_DOMAINS,
    RESEARCH_AGENT_OUTPUT_KEYS,
    SECTION_B_WEIGHTS,
)


def compute_agent_output_coverage(session_state: dict[str, Any]) -> float:
    total = len(RESEARCH_AGENT_OUTPUT_KEYS)
    if total == 0:
        return 0.0
    populated = sum(
        1
        for key in RESEARCH_AGENT_OUTPUT_KEYS.values()
        if session_state.get(key) and str(session_state.get(key)).strip()
    )
    return populated / total


def compute_evidence_breadth(job_evidence: list[dict]) -> float:
    urls = [
        entry.get("url", "") for entry in job_evidence if entry.get("url", "").strip()
    ]
    unique_domains = count_unique_domains(urls)
    return min(1.0, unique_domains / MIN_EXPECTED_DOMAINS)


def compute_groundedness(
    final_report: str,
    job_evidence: list[dict] | None = None,
) -> float:
    section_13 = extract_section_13(final_report)
    if not section_13:
        logger.warning("[Evaluation] Section 13 (Source Summary) not found in report")
        return 0.0

    cited_urls = extract_urls(section_13)
    evidence = job_evidence or []
    if evidence:
        cached_domains = set()
        for item in evidence:
            url = (item.get("url") or "").strip().lower()
            if not url:
                continue
            try:
                netloc = urlparse(url).netloc.lower().removeprefix("www.")
                if netloc:
                    cached_domains.add(netloc)
            except Exception:
                pass

        verified = 0
        for url in cited_urls:
            try:
                netloc = urlparse(url).netloc.lower().removeprefix("www.")
                if netloc and netloc in cached_domains:
                    verified += 1
            except Exception:
                pass

        score = min(1.0, verified / MIN_EXPECTED_DOMAINS)
        logger.debug(
            f"[Evaluation] Citation groundedness: {verified} cited domains "
            f"in job_evidence ({len(cached_domains)} domains) -> {score:.3f}"
        )
        return score

    unique_domains = count_unique_domains(cited_urls)
    score = min(1.0, unique_domains / MIN_EXPECTED_DOMAINS)
    logger.debug(
        f"[Evaluation] Citation groundedness (no evidence): "
        f"{unique_domains} domains in Section 13 -> {score:.3f}"
    )
    return score


def compute_completeness(final_report: str) -> float:
    expected_headers = [
        "Company Snapshot",
        "Company Overview",
        "Global Operations",
        "Key Executive Bios",
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

    report_lower = final_report.lower()
    populated = 0
    for header in expected_headers:
        if header.lower() in report_lower:
            idx = report_lower.find(header.lower())
            section_slice = final_report[idx : idx + 500].lower()
            if "publicly unavailable" not in section_slice or len(section_slice) > 200:
                populated += 1

    completeness = populated / EXPECTED_SECTION_COUNT
    logger.debug(
        f"[Evaluation] Completeness: {populated}/{EXPECTED_SECTION_COUNT} sections populated -> {completeness:.3f}"
    )
    return completeness


def build_section_b_result(
    *,
    m1: float,
    m2: float,
    m3: float,
    m4: float,
    m5: float,
) -> dict[str, Any]:
    section_b_score = (
        m1 * SECTION_B_WEIGHTS["M1_agent_output_coverage"]
        + m2 * SECTION_B_WEIGHTS["M2_report_completeness"]
        + m3 * SECTION_B_WEIGHTS["M3_citation_groundedness"]
        + m4 * SECTION_B_WEIGHTS["M4_evidence_breadth"]
        + m5 * SECTION_B_WEIGHTS["M5_semantic_groundedness"]
    ) * 100

    return {
        "M1_agent_output_coverage": m1,
        "M1_weight": SECTION_B_WEIGHTS["M1_agent_output_coverage"],
        "M2_report_completeness": m2,
        "M2_weight": SECTION_B_WEIGHTS["M2_report_completeness"],
        "M2_sections_expected": EXPECTED_SECTION_COUNT,
        "M3_citation_groundedness": m3,
        "M3_weight": SECTION_B_WEIGHTS["M3_citation_groundedness"],
        "M3_method": "Section 13 cited domains matching job_evidence",
        "M4_evidence_breadth": m4,
        "M4_weight": SECTION_B_WEIGHTS["M4_evidence_breadth"],
        "M4_min_expected_domains": MIN_EXPECTED_DOMAINS,
        "M5_semantic_groundedness": m5,
        "M5_weight": SECTION_B_WEIGHTS["M5_semantic_groundedness"],
        "M5_threshold": settings.EVAL_EMBEDDING_SIMILARITY_THRESHOLD,
        "section_b_score": round(section_b_score, 2),
        "section_b_weight": 0.20,
        "scoring_version": "v2",
    }


def extract_section_13(report: str) -> str:
    patterns = [
        r"##\s*13\.?\s*Source\s+Summary(.*?)(?=\n##|\Z)",
        r"##\s*Source\s+Summary(.*?)(?=\n##|\Z)",
    ]
    for pattern in patterns:
        match = re.search(pattern, report, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
    return ""


def extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s\)\]\,\"\'\<\>]+", text)


def count_unique_domains(urls: list[str]) -> int:
    domains = set()
    for url in urls:
        try:
            parsed = urlparse(url)
            if parsed.netloc:
                domain = parsed.netloc.lower().removeprefix("www.")
                domains.add(domain)
        except Exception:
            pass
    return len(domains)
