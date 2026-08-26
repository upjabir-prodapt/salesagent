"""
Input and Output Guardrails for Sales Agent.

Input guardrails (applied at API boundary and before every LLM call):
  - PII detection: email, phone, SSN, credit card, IPv4, passport patterns
  - Jailbreak / prompt-injection detection: known adversarial phrases and tokens

Output guardrails (available checks for compiled report):
  - Strategic Brief format validation: required top-level headers + section table checks
  - Optional checks kept for compatibility (narrative bullets, completeness,
    prohibited content, hallucination) but not part of the active blocking path.
"""

import json
import re
from dataclasses import dataclass, field

from src.shared.config import settings
from src.shared.exceptions import InputValidationException
from src.shared.logging_config import logger
from src.shared.repositories.clients import get_genai_client


class _ClientPool:
    """Compatibility shim for older tests patching client_pool.get_genai_client."""

    @staticmethod
    def get_genai_client():
        return get_genai_client()


client_pool = _ClientPool()

# ---------------------------------------------------------------------------
# PII patterns — label → regex
# ---------------------------------------------------------------------------

_PII_PATTERNS: list[tuple[str, str]] = [
    ("email", r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"),
    ("phone", r"(\+\d{1,3}[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}"),
    ("ssn", r"\b\d{3}-\d{2}-\d{4}\b"),
    ("credit_card", r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
    ("ipv4", r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    ("passport", r"\b[A-Z]{1,2}\d{6,9}\b"),
]

# ---------------------------------------------------------------------------
# Jailbreak / prompt-injection patterns — label → regex
# ---------------------------------------------------------------------------

_JAILBREAK_PATTERNS: list[tuple[str, str]] = [
    ("ignore_instructions", r"ignore\s+(previous|all|your)\s+instructions?"),
    (
        "forget_guidelines",
        r"forget\s+(your|all|previous)\s+(instructions?|guidelines?|rules?|constraints?)",
    ),
    ("act_as", r"(you\s+are\s+now|pretend\s+(to\s+be|you\s+are))"),
    ("dan_mode", r"\bdan\s+mode\b"),
    ("jailbreak_keyword", r"\bjailbreak\b"),
    (
        "bypass_safety",
        r"(bypass|override|circumvent|disregard)\s+(safety|guidelines?|rules?|filters?|restrictions?)",
    ),
    (
        "do_anything_now",
        r"(do\s+anything\s+now|no\s+restrictions|without\s+restrictions)",
    ),
    ("prompt_injection", r"(system\s*prompt|prompt\s*injection)"),
    (
        "special_mode",
        r"(developer\s+mode|god\s+mode|unrestricted\s+mode|jailbreak\s+mode)",
    ),
    ("respond_as", r"respond\s+(only|exclusively)\s+as"),
    ("ignore_ethics", r"(ignore|disregard)\s+(ethical|moral|safety)"),
    ("xml_system_tag", r"<\s*/?\s*system\s*>"),
    ("llm_special_tokens", r"\[INST\]|\[\/INST\]|<\|im_start\|>|<\|im_end\|>"),
    ("markdown_override", r"###\s*(SYSTEM|INSTRUCTION|OVERRIDE|ADMIN)"),
]

# ---------------------------------------------------------------------------
# Output: sections where bullet points are NOT allowed (prose only)
# ---------------------------------------------------------------------------

# Section heading fragments to match in the compiled markdown report
_NARRATIVE_SECTIONS: list[str] = [
    "9. Relationship Landscape",
    "12. Signals",
]

# Required top-level section header patterns for Strategic Brief validation.
# These mirror the report compiler prompt structure.
_REQUIRED_HEADERS: list[tuple[str, str]] = [
    ("Company Snapshot", r"##\s+Company Snapshot"),
    ("Section 1 (Company Overview)", r"##\s+1\.\s+Company Overview"),
    ("Section 2 (Key Executive Bios)", r"##\s+2\.\s+Key Executive Bios"),
    (
        "Section 3 (Strategic Priorities and Business Goals)",
        r"##\s+3\.\s+Strategic Priorities and Business Goals\s+\(Next 2-5 Years\)",
    ),
    (
        "Section 4 (Current Market Position & Outlook)",
        r"##\s+4\.\s+Current Market Position & Outlook",
    ),
    ("Section 5 (Technology Landscape)", r"##\s+5\.\s+Technology Landscape"),
    (
        "Section 6 (Key Business & IT Challenges)",
        r"##\s+6\.\s+Key Business & IT Challenges",
    ),
    (
        "Section 7 (Procurement & Technology Buying Patterns)",
        r"##\s+7\.\s+Procurement & Technology Buying Patterns",
    ),
    (
        "Section 8 (Colt Technology Alignment Table)",
        r"##\s+8\.\s+Colt Technology Alignment Table",
    ),
    (
        "Section 9 (Relationship Landscape & Potential Synergies)",
        r"##\s+9\.\s+Relationship Landscape & Potential Synergies",
    ),
    (
        "Section 10 (Regional Spend & Infrastructure Overlay)",
        r"##\s+10\.\s+Regional Spend & Infrastructure Overlay",
    ),
    (
        "Section 11 (Strategic Opportunity & Live Call Readiness)",
        r"##\s+11\.\s+Strategic Opportunity & Live Call Readiness",
    ),
    ("Section 12 (Signals)", r"##\s+12\.\s+Signals"),
    ("Section 13 (Source Summary)", r"##\s+13\.\s+Source Summary"),
]

_BULLET_LINE_RE = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)

# Sections that must contain markdown tables.
_TABLE_REQUIRED_HEADERS: list[tuple[str, str]] = [
    (
        "Section 8 (Colt Technology Alignment Table)",
        r"##\s+8\.\s+Colt Technology Alignment Table",
    ),
]

# ---------------------------------------------------------------------------
# Output: completeness — 13 expected section headers
# ---------------------------------------------------------------------------

_COMPLETENESS_SECTION_HEADERS: list[str] = [
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
_TOTAL_SECTIONS = 13

# ---------------------------------------------------------------------------
# Output: prohibited content patterns — label → regex
# ---------------------------------------------------------------------------

_PROHIBITED_CONTENT_PATTERNS: list[tuple[str, str]] = [
    ("insider_information", r"\binsider\s+(information|trading|knowledge|tip)\b"),
    ("mnpi", r"\b(material\s+non[\s\-]?public\s+information|MNPI)\b"),
    (
        "non_public_info",
        r"\bnon[\s\-]?public\s+(information|data|intelligence|details)\b",
    ),
    (
        "buy_recommendation",
        r"\b(buy|strong[\s\-]buy)\s+recommendation\b|\brecommend(?:ation)?\s+to\s+buy\b",
    ),
    (
        "sell_recommendation",
        r"\b(sell|strong[\s\-]sell)\s+recommendation\b|\brecommend(?:ation)?\s+to\s+sell\b",
    ),
]


# ---------------------------------------------------------------------------
# Shared data types
# ---------------------------------------------------------------------------


@dataclass
class GuardrailViolation:
    rule: str
    detail: str


@dataclass
class OutputValidationResult:
    is_valid: bool
    violations: list[GuardrailViolation] = field(default_factory=list)

    def _add(self, rule: str, detail: str) -> None:
        self.violations.append(GuardrailViolation(rule=rule, detail=detail))
        self.is_valid = False


# ---------------------------------------------------------------------------
# Input Guardrail
# ---------------------------------------------------------------------------


class InputGuardrail:
    """Scan user-supplied text for PII and jailbreak/prompt-injection attempts."""

    def scan_pii(self, text: str) -> list[GuardrailViolation]:
        """Return violations for each PII pattern found."""
        violations = []
        for label, pattern in _PII_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append(
                    GuardrailViolation(
                        rule=f"pii:{label}",
                        detail=f"Detected {label} pattern in input",
                    )
                )
        return violations

    def scan_jailbreak(self, text: str) -> list[GuardrailViolation]:
        """Return violations for each jailbreak/injection pattern found."""
        violations = []
        for label, pattern in _JAILBREAK_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append(
                    GuardrailViolation(
                        rule=f"jailbreak:{label}",
                        detail=f"Detected adversarial pattern '{label}' in input",
                    )
                )
        return violations

    def validate(self, text: str, field_name: str = "input") -> None:
        """
        Run PII and jailbreak scans. Raises InputValidationException on any hit.

        Args:
            text: The text to scan (e.g. company_name).
            field_name: Label used in logs and the exception message.
        """
        violations = self.scan_pii(text) + self.scan_jailbreak(text)
        if violations:
            rules = ", ".join(v.rule for v in violations)
            logger.warning(
                f"[InputGuardrail] Blocked field={field_name!r} violations={rules}"
            )
            raise InputValidationException(
                message=f"Input blocked by guardrails: {rules}",
                field=field_name,
                value=text[:80],
            )
        logger.debug(f"[InputGuardrail] field={field_name!r} passed all checks")


# ---------------------------------------------------------------------------
# Agent Guardrail (Iterative)
# ---------------------------------------------------------------------------


class AgentGuardrail:
    """Scan individual agent outputs for PII and prohibited content."""

    def scan_pii(self, text: str) -> list[GuardrailViolation]:
        """Return violations for each PII pattern found."""
        violations = []
        for label, pattern in _PII_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append(
                    GuardrailViolation(
                        rule=f"agent:pii:{label}",
                        detail=f"Detected {label} pattern in agent output",
                    )
                )
        return violations

    def scan_prohibited_content(self, text: str) -> list[GuardrailViolation]:
        """Return violations for prohibited content (insider info, buy/sell recs)."""
        violations = []
        for label, pattern in _PROHIBITED_CONTENT_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                violations.append(
                    GuardrailViolation(
                        rule=f"agent:prohibited:{label}",
                        detail=f"Agent output contains prohibited content pattern '{label}'",
                    )
                )
        return violations

    def validate(self, text: str, agent_name: str = "unknown") -> None:
        """
        Run PII and prohibited content scans on agent output.
        Raises InputValidationException on any hit to block the pipeline.
        """
        violations = self.scan_pii(text) + self.scan_prohibited_content(text)
        if violations:
            rules = ", ".join(v.rule for v in violations)
            logger.warning(
                f"[AgentGuardrail] Blocked agent={agent_name!r} violations={rules}"
            )
            # Reusing InputValidationException as it results in a 400/500 depending on handler
            raise InputValidationException(
                message=f"Agent {agent_name} output blocked by guardrails: {rules}",
                field=agent_name,
                value=text[:100],
            )
        logger.debug(f"[AgentGuardrail] agent={agent_name!r} passed all checks")


# ---------------------------------------------------------------------------
# Output Guardrail
# ---------------------------------------------------------------------------


class OutputGuardrail:
    """Validate the final markdown report for format and narrative quality."""

    def _extract_section_body(self, report: str, heading_fragment: str) -> str:
        """
        Extract the body text of a section matched by heading_fragment,
        up to the next ## heading or end of string.
        """
        pattern = (
            rf"##[^#].*?{re.escape(heading_fragment)}.*?\n"
            rf"(.*?)(?=\n##[^#]|\Z)"
        )
        m = re.search(pattern, report, re.DOTALL | re.IGNORECASE)
        return m.group(1) if m else ""

    def check_narrative_bullets(self, report: str) -> list[GuardrailViolation]:
        """
        Detect bullet points in sections that must be written as prose paragraphs.
        Sections checked: 9. Relationship Landscape, 12. Signals.
        """
        violations = []
        for section_name in _NARRATIVE_SECTIONS:
            body = self._extract_section_body(report, section_name)
            if not body:
                continue
            bullet_hits = _BULLET_LINE_RE.findall(body)
            if bullet_hits:
                violations.append(
                    GuardrailViolation(
                        rule="output:narrative_bullets",
                        detail=(
                            f"Section '{section_name}' contains {len(bullet_hits)} "
                            f"bullet-point line(s) — expected prose paragraphs only"
                        ),
                    )
                )
        return violations

    def check_strategic_brief_format(self, report: str) -> list[GuardrailViolation]:
        """
        Verify all required top-level Strategic Brief section headers are present
        and that required sections contain a markdown table.
        """
        violations = []

        for label, header_pattern in _REQUIRED_HEADERS:
            if not re.search(
                rf"^\s*{header_pattern}\s*$", report, re.IGNORECASE | re.MULTILINE
            ):
                violations.append(
                    GuardrailViolation(
                        rule="output:missing_section",
                        detail=f"Missing required section: {label} (pattern: {header_pattern!r})",
                    )
                )

        for label, header_pattern in _TABLE_REQUIRED_HEADERS:
            body = self._extract_section_body_by_pattern(report, header_pattern)
            if not body:
                continue
            if not re.search(r"^\s*\|.+\|\s*$", body, re.MULTILINE):
                violations.append(
                    GuardrailViolation(
                        rule="output:missing_table",
                        detail=f"Required section is missing markdown table rows: {label}",
                    )
                )

        return violations

    @staticmethod
    def _extract_section_body_by_pattern(report: str, heading_pattern: str) -> str:
        """Extract body for an exact top-level heading regex pattern."""
        header_match = re.search(
            rf"^\s*{heading_pattern}\s*$",
            report,
            re.IGNORECASE | re.MULTILINE,
        )
        if not header_match:
            return ""

        section_start = header_match.end()
        next_heading_match = re.search(
            r"^\s*##\s+[^#].*$",
            report[section_start:],
            re.MULTILINE,
        )
        section_end = (
            section_start + next_heading_match.start()
            if next_heading_match
            else len(report)
        )
        return report[section_start:section_end]

    def check_completeness(self, report: str) -> list[GuardrailViolation]:
        """
        Verify that at least OUTPUT_GUARDRAIL_MIN_SECTIONS of the 13 expected
        sections are populated (not empty or marked as publicly unavailable).
        """
        report_lower = report.lower()
        populated = 0
        for header in _COMPLETENESS_SECTION_HEADERS:
            if header.lower() in report_lower:
                idx = report_lower.find(header.lower())
                section_slice = report[idx : idx + 500].lower()
                if (
                    "publicly unavailable" not in section_slice
                    or len(section_slice) > 200
                ):
                    populated += 1

        if populated < settings.OUTPUT_GUARDRAIL_MIN_SECTIONS:
            return [
                GuardrailViolation(
                    rule="output:incomplete_report",
                    detail=(
                        f"Only {populated}/{_TOTAL_SECTIONS} sections are populated; "
                        f"minimum required is {settings.OUTPUT_GUARDRAIL_MIN_SECTIONS}"
                    ),
                )
            ]
        return []

    def check_prohibited_content(self, report: str) -> list[GuardrailViolation]:
        """
        Scan for prohibited content: insider information references,
        buy recommendations, and sell recommendations.
        """
        violations = []
        for label, pattern in _PROHIBITED_CONTENT_PATTERNS:
            if re.search(pattern, report, re.IGNORECASE):
                violations.append(
                    GuardrailViolation(
                        rule=f"output:prohibited:{label}",
                        detail=f"Report contains prohibited content pattern '{label}'",
                    )
                )
        return violations

    async def check_hallucinations(
        self,
        report: str,
        raw_search_cache: list[dict] | None = None,
        session_state: dict | None = None,
    ) -> list[GuardrailViolation]:
        """
        Use a secondary Gemini Flash model to fact-check the full report against
        raw web-scraped evidence cached during the research pipeline.

        When raw_search_cache is provided (the preferred path), every claim in the
        report is verified against actual scraped snippets — including numerical facts
        such as revenues, headcounts, and dates that a judge LLM may not know from
        its own training data.

        Falls back to the legacy Section 11 vs Section 12 cross-reference when the
        cache is absent or empty.

        Failures in this check are non-fatal (logged and skipped).
        """
        use_cache = bool(raw_search_cache)

        if use_cache:
            return await self._check_hallucinations_with_cache(
                report,
                raw_search_cache,
                session_state=session_state,  # type: ignore[arg-type]
            )
        else:
            logger.debug(
                "[OutputGuardrail] raw_search_cache empty — falling back to "
                "Section 11 vs Section 12 hallucination check"
            )
            return await self._check_hallucinations_legacy(
                report, session_state=session_state
            )

    async def _check_hallucinations_with_cache(
        self,
        report: str,
        raw_search_cache: list[dict],
        session_state: dict | None = None,
    ) -> list[GuardrailViolation]:
        """
        Primary hallucination check: verify report claims against raw search snippets.

        Builds a condensed evidence block (≤ 12 000 chars) from the cache, then asks
        Gemini Flash to identify every claim in the report that cannot be verified or
        is contradicted by that evidence.  Numerical facts (revenue, headcount, dates,
        growth rates) are explicitly included in the audit scope.
        """
        evidence_block = self._build_evidence_block(raw_search_cache, max_chars=12000)
        if not evidence_block:
            logger.debug(
                "[OutputGuardrail] Evidence block is empty after building — skipping cache check"
            )
            return []

        try:
            from google.genai import types as genai_types

            client = client_pool.get_genai_client()

            prompt = (
                "You are a strict fact-checking auditor for a B2B sales intelligence report.\n\n"
                "The report below was generated by an AI agent that searched the web during this "
                "job run. The VERIFIED EVIDENCE section contains the actual web snippets that were "
                "scraped — this is the ONLY authoritative source of truth.\n\n"
                "Your task: identify every factual claim in the report that CANNOT be verified "
                "against the provided evidence OR that is directly contradicted by it.\n\n"
                "## VERIFIED EVIDENCE (raw web-scraped snippets from this job):\n"
                f"{evidence_block}\n\n"
                "## REPORT TO AUDIT (all 13 sections):\n"
                f"{report[:12000]}\n\n"
                "## AUDIT INSTRUCTIONS:\n"
                "A claim is UNSUPPORTED ONLY if:\n"
                "  - The VERIFIED EVIDENCE specifically provides a DIFFERENT number or fact (Direct Contradiction).\n"
                "  - The claim is wildly implausible (e.g. a small regional firm having trillions in revenue).\n"
                "  - The claim is a specific, high-stakes fact (like a specific named CEO) that is not in the evidence AND is not found in common public knowledge.\n"
                "\n"
                "IMPORTANT GUIDELINES:\n"
                "  - **IGNORE YOUR INTERNAL TRAINING DATA**: Company financials change every quarter. If the report says revenue is $50B and your training data says $40B, TRUST THE REPORT. Do not flag it as a hallucination unless the VERIFIED EVIDENCE explicitly shows a different number.\n"
                "  - **LENIENCY FOR MISSING EVIDENCE**: The VERIFIED EVIDENCE is a sample. If a claim (like a revenue figure) is missing from the evidence but is not contradicted by it, do NOT flag it as a hallucination unless it feels fabricated or implausible.\n"
                "  - **Numerical Facts**: Allow for small rounding differences or different reporting periods.\n"
                "DO NOT flag:\n"
                "  - **Strategic Suggestions or Recommendations**: Any advice, suggested strategy, or proposed solution for the target company is an analytical output and should NOT be flagged.\n"
                "  - **Mentions of Incumbent Providers**: References to existing vendors (e.g. BT, Orange, Vodafone) are standard sales intelligence and should NOT be flagged.\n"
                "  - **Sales Hypotheses / Pain Points**: Claims about 'aging infrastructure', 'legacy bottlenecks', or 'performance gaps' are strategic displacement angles and do not require specific citations.\n"
                "  - Colt's own product descriptions or capabilities (these are about Colt, not "
                "the target company, and are not verifiable from scraped evidence).\n"
                "  - Minor formatting or phrasing differences where the underlying fact is present.\n"
                "  - Reasonable inferences that follow logically from evidenced facts.\n"
                "  - Competitive displacement arguments or sales inferences "
                "('opportunity to displace an incumbent', 'underserved by current provider', etc.).\n"
                "  - Claims that describe the ABSENCE of a known provider or announcement — "
                "absence of evidence in the search cache is not a hallucinated claim.\n\n"
                "## OUTPUT FORMAT:\n"
                "Return ONLY a valid JSON object with this exact structure:\n"
                "{\n"
                '  "has_unsupported_claims": <bool>,\n'
                '  "unsupported_count": <int>,\n'
                '  "category_results": {\n'
                '    "numerical_facts": {"supported": <bool>, "issues": ["<description>"]},\n'
                '    "named_events": {"supported": <bool>, "issues": []},\n'
                '    "executive_details": {"supported": <bool>, "issues": []},\n'
                '    "urgency_trends": {"supported": <bool>, "issues": []},\n'
                '    "regulatory_claims": {"supported": <bool>, "issues": []},\n'
                '    "competitive_claims": {"supported": <bool>, "issues": []}\n'
                "  },\n"
                '  "examples": ["<exact unsupported claim quoted verbatim from the report>"]\n'
                "}"
            )

            response = client.models.generate_content(
                model=settings.OUTPUT_GUARDRAIL_HALLUCINATION_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            if session_state is not None:
                from src.worker.runtime.pricing import (
                    record_genai_response_usage,
                )

                record_genai_response_usage(
                    session_state,
                    settings.OUTPUT_GUARDRAIL_HALLUCINATION_MODEL,
                    response,
                )

            raw = response.text.strip() if response.text else ""
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)

            result = json.loads(raw)
            count = int(result.get("unsupported_count", 0))
            block_threshold = settings.OUTPUT_GUARDRAIL_HALLUCINATION_BLOCK_THRESHOLD
            if result.get("has_unsupported_claims") and count >= block_threshold:
                examples = result.get("examples", [])
                category_results = result.get("category_results", {})
                failed_categories = [
                    cat
                    for cat, details in category_results.items()
                    if not details.get("supported", True)
                ]
                return [
                    GuardrailViolation(
                        rule="output:hallucination",
                        detail=(
                            f"Report contains {count} claim(s) unsupported by scraped evidence "
                            f"across categories: {', '.join(failed_categories) or 'unknown'}. "
                            f"Examples: {'; '.join(examples[:3])}"
                        ),
                    )
                ]
        except Exception as exc:
            logger.warning(
                f"[OutputGuardrail] Cache-based hallucination check failed (non-fatal): {exc}"
            )

        return []

    async def _check_hallucinations_legacy(
        self, report: str, session_state: dict | None = None
    ) -> list[GuardrailViolation]:
        """
        Fallback hallucination check (no cache): cross-references Section 11 claims
        against Section 12 citations — the original behaviour before cache was available.
        """
        section_11 = self._extract_section_body(report, "11.")
        section_12 = self._extract_section_body(report, "12.")

        if not section_11 or not section_12:
            logger.debug(
                "[OutputGuardrail] Skipping legacy hallucination check: Section 11 or 12 not found"
            )
            return []

        try:
            from google.genai import types as genai_types

            client = client_pool.get_genai_client()

            prompt = (
                "You are a strict fact-checking auditor for a B2B sales intelligence report.\n\n"
                "Your task is to verify that every claim in Section 11 (Strategic Opportunity) "
                "is directly evidenced by concrete signals, data points, or citations present in "
                "Section 12 (Signals) OR by an explicit inline citation within Section 11 itself "
                '(e.g., `[Source: marketagent_output — "..."]`). Section 11 is written by a '
                "synthesis agent that may hallucinate urgency, regulatory events, or AI initiatives "
                "that were never found in the underlying research.\n\n"
                "## Section 11 — Strategic Opportunity & Live Call Readiness:\n"
                f"{section_11[:4000]}\n\n"
                "## Section 12 — Signals & Citations:\n"
                f"{section_12[:4000]}\n\n"
                "## AUDIT INSTRUCTIONS:\n"
                "For EACH of the following sub-categories in Section 11, independently check "
                "whether the claims are supported by Section 12 OR by an explicit inline citation "
                "within Section 11. Use extreme leniency for strategic sales narratives.\n\n"
                "A claim is UNSUPPORTED ONLY if:\n"
                "  - It references a specific event (fine, breach, acquisition) that is DIRECTLY CONTRADICTED "
                "by a signal in Section 12.\n"
                "  - It uses a specific figure (revenue, headcount) that is wildly different (e.g. 10x different) "
                "from any figures present in Section 12.\n"
                "  - The claim is a gross fabrication (e.g. claiming the company sells spaceships when it sells groceries).\n\n"
                "DO NOT flag as unsupported:\n"
                "  - **Directional Sales Hooks**: Claims like 'catastrophic risk', 'critical inflection point', "
                "or 'massive investment' are analytical interpretations and should NOT be flagged.\n"
                "  - **Specific Targets**: If the report mentions a figure like '£400m in savings' or '10% growth', "
                "treat it as a valid sales hypothesis even if the exact number is missing from Section 12.\n"
                "  - **Named Strategies**: Do not flag plausible-sounding strategy names (e.g. 'Reshaping for Growth') "
                "even if the search snippet is missing.\n"
                "  - **Claims with explicit inline citations**: If a bullet point ends with "
                '`[Source: <agent_output_key> — "<exact data point or quote>"]`, it is ALWAYS VALID.\n'
                "  - **Strategic Suggestions or Recommendations**: Any advice or proposed solution.\n"
                "  - **Mentions of Incumbent Providers**: References to existing vendors (e.g. BT, Orange, Vodafone).\n"
                "  - **Sales Hypotheses / Pain Points**: Claims about 'aging infrastructure' or 'legacy bottlenecks'.\n"
                "  - Competitive displacement arguments or sales inferences "
                "(e.g. 'opportunity to displace an incumbent', 'underserved by current provider') "
                "— these are strategic conclusions, not factual claims requiring a citation.\n"
                "  - Claims that describe the ABSENCE of a known provider or announcement "
                "(absence of evidence in search results cannot be treated as a hallucinated claim).\n"
                "  - Colt product descriptions or capability statements — these describe Colt's "
                "offering, not the target company, and are not verifiable from scraped evidence.\n"
                "  - Content placed under 'Regulatory Triggers' that is actually a competitive or "
                "strategic observation — categorise it as competitive_displacement instead and do "
                "not flag it as unsupported.\n\n"
                "Sub-categories to check:\n"
                "  1. Hooks\n"
                "  2. Executive Narratives\n"
                "  3. Regulatory Triggers — a claim is SUPPORTED if Section 12 OR an inline citation "
                "contains ANY mention of the same regulatory body, investigation, fine, framework, "
                "or deadline, even if the exact wording differs. For example: 'under ICO investigation' "
                "is supported if the source mentions the ICO, a data-protection probe, or any related "
                "UK data-privacy enforcement action.\n"
                "  4. AI Urgency\n"
                "  5. Competitive Displacement Angles\n"
                "  6. Clear Colt Differentiation (only flag if Colt product claims are presented "
                "as facts about the TARGET company rather than Colt's offering)\n\n"
                "## OUTPUT FORMAT:\n"
                "Return ONLY a valid JSON object with this exact structure:\n"
                "{\n"
                '  "has_unsupported_claims": <bool>,\n'
                '  "unsupported_count": <int>,\n'
                '  "category_results": {\n'
                '    "hooks": {"supported": <bool>, "issues": ["<issue description>"]},\n'
                '    "executive_narratives": {"supported": <bool>, "issues": []},\n'
                '    "regulatory_triggers": {"supported": <bool>, "issues": []},\n'
                '    "ai_urgency": {"supported": <bool>, "issues": []},\n'
                '    "competitive_displacement": {"supported": <bool>, "issues": []},\n'
                '    "colt_differentiation": {"supported": <bool>, "issues": []}\n'
                "  },\n"
                '  "examples": ["<exact unsupported claim from Section 11 — quote it verbatim>"]\n'
                "}"
            )

            response = client.models.generate_content(
                model=settings.OUTPUT_GUARDRAIL_HALLUCINATION_MODEL,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.0,
                ),
            )
            if session_state is not None:
                from src.worker.runtime.pricing import (
                    record_genai_response_usage,
                )

                record_genai_response_usage(
                    session_state,
                    settings.OUTPUT_GUARDRAIL_HALLUCINATION_MODEL,
                    response,
                )

            raw = response.text.strip() if response.text else ""
            if raw.startswith("```"):
                raw = re.sub(r"^```(?:json)?\n?", "", raw)
                raw = re.sub(r"\n?```$", "", raw)

            result = json.loads(raw)
            count = int(result.get("unsupported_count", 0))
            block_threshold = settings.OUTPUT_GUARDRAIL_HALLUCINATION_BLOCK_THRESHOLD
            if result.get("has_unsupported_claims") and count >= block_threshold:
                examples = result.get("examples", [])
                category_results = result.get("category_results", {})
                failed_categories = [
                    cat
                    for cat, details in category_results.items()
                    if not details.get("supported", True)
                ]
                return [
                    GuardrailViolation(
                        rule="output:hallucination",
                        detail=(
                            f"Section 11 contains {count} unsupported claim(s) across "
                            f"categories: {', '.join(failed_categories) or 'unknown'}. "
                            f"Examples: {'; '.join(examples[:3])}"
                        ),
                    )
                ]
        except Exception as exc:
            logger.warning(
                f"[OutputGuardrail] Legacy hallucination check failed (non-fatal): {exc}"
            )

        return []

    @staticmethod
    def _build_evidence_block(
        raw_search_cache: list[dict],
        max_chars: int = 12000,
    ) -> str:
        """
        Build a condensed plaintext evidence block from the raw search cache for
        inclusion in the hallucination-check prompt.

        Each entry is rendered as:
            [Agent: <agent>] <title>
            URL: <url>
            <snippet>

        Entries are included in insertion order (i.e. the order agents searched)
        until max_chars is reached.
        """
        lines: list[str] = []
        total = 0
        for entry in raw_search_cache:
            agent = entry.get("agent", "")
            title = entry.get("title", "").strip()
            url = entry.get("url", "").strip()
            snippet = entry.get("snippet", "").strip()

            block = f"[Agent: {agent}] {title}\nURL: {url}\n{snippet}\n"
            if total + len(block) > max_chars:
                break
            lines.append(block)
            total += len(block)

        return "\n".join(lines)

    async def validate(
        self,
        report: str,
        raw_search_cache: list[dict] | None = None,
    ) -> OutputValidationResult:
        """
        Run active output guardrails and return a consolidated result.
        Does NOT raise — callers decide whether to treat violations as fatal.

        Active blocking check:
          1. Strategic Brief format:
             - required top-level section headers
             - markdown table presence in required section(s)

        Args:
            report: The final compiled markdown report.
            raw_search_cache: Kept for backward compatibility with callers.
                Currently unused by the active blocking checks.
        """
        result = OutputValidationResult(is_valid=True)

        for v in self.check_strategic_brief_format(report):
            result._add(v.rule, v.detail)

        if result.is_valid:
            logger.info("[OutputGuardrail] Report passed all output validation checks")
        else:
            for v in result.violations:
                logger.warning(f"[OutputGuardrail] BLOCKED — {v.rule}: {v.detail}")

        return result
