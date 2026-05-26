"""
Evaluation Service - Automated and LLM-based Report Quality Evaluation

Two-section evaluation framework (scoring_version v2):
  Section A (80%): LLM-as-judge scoring 14 human dimensions (D1-D14) with weights,
                   plus binary penalties for hallucinations (M12) and policy violations (M13).
  Section B (20%): Agent coverage, completeness, citation groundedness, evidence breadth,
                   semantic groundedness (ONNX embeddings).

Results are stored as evaluation.json in GCS alongside raw_data.json and final_report.md.
"""

import asyncio
import json
import re
import textwrap
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from ....core.config import settings
from ....core.logging_config import logger
from ....dependencies.service_dependencies import get_genai_client
from ...catalog.search import colt_product_search
from .sales.utils.embedding_similarity import compute_semantic_groundedness
from .sales.utils.evidence import (
    aggregate_job_evidence,
    evidence_to_block,
    format_agent_outputs_for_judge,
)
from .utils.agent_pipeline import AGENT_OUTPUT_KEYS

# ---------------------------------------------------------------------------
# Dimension weights for Section A (D1–D14)
# ---------------------------------------------------------------------------
DIMENSION_CONFIG: dict[str, dict[str, Any]] = {
    "D1_data_currency_recency": {
        "weight": 2.0,
        "category": "Factual Accuracy",
        "description": "Data Currency & Recency — Stale data is actively harmful to sales conversations",
    },
    "D2_executive_intelligence_bios": {
        "weight": 2.0,
        "category": "Stakeholder Mapping",
        "description": "Executive Intelligence & Bios — Accurate decision-maker mapping is critical",
    },
    "D3_cybersecurity_context": {
        "weight": 2.0,
        "category": "Pain Point ID",
        "description": "Cybersecurity Context — Direct alignment with Colt's core offering domain",
    },
    "D4_strategic_priorities_alignment": {
        "weight": 1.5,
        "category": "Business Drivers",
        "description": "Strategic Priorities Alignment — Foundation for 'Why Now?' narrative",
    },
    "D5_technology_landscape_detail": {
        "weight": 1.5,
        "category": "Tech Stack Mapping",
        "description": "Technology Landscape Detail — Identifies displacement opportunities for Colt",
    },
    "D6_colt_solution_alignment_table": {
        "weight": 2.0,
        "category": "Pitch Readiness",
        "description": "Colt Solution Alignment Table — Directly measures RAG effectiveness and output utility",
    },
    "D7_procurement_buying_signals": {
        "weight": 1.5,
        "category": "Sales Timing",
        "description": "Procurement & Buying Signals — Enables sellers to time outreach effectively",
    },
    "D8_financial_trading_relevance": {
        "weight": 1.5,
        "category": "Commercial Context",
        "description": "Financial & Trading Relevance — Supports business case construction",
    },
    "D9_global_operations_footprint": {
        "weight": 1.0,
        "category": "Geographic Targeting",
        "description": "Global Operations & Footprint — Useful but secondary to core intelligence",
    },
    "D10_regulatory_compliance_detail": {
        "weight": 1.0,
        "category": "Risk Angle",
        "description": "Regulatory & Compliance Detail — Context for compliance-driven sales motions",
    },
    "D11_relationship_ecosystem_mapping": {
        "weight": 1.0,
        "category": "Partner Landscape",
        "description": "Relationship & Ecosystem Mapping — Useful for partnership and channel strategy",
    },
    "D12_sustainability_esg_alignment": {
        "weight": 1.0,
        "category": "Value Hook",
        "description": "Sustainability / ESG Alignment — Increasingly important, but not primary buying driver",
    },
    "D13_signals_growth_risk_campaign": {
        "weight": 1.0,
        "category": "Real-Time Intel",
        "description": "Signals (Growth, Risk, Campaign) — Provides urgency and timeliness context",
    },
    "D14_why_colt_why_now_summary": {
        "weight": 1.5,
        "category": "Closing Narrative",
        "description": "Why Colt? Why Now? Summary — Directly impacts seller confidence and pitch quality",
    },
}

# Maximum possible weighted score for Section A:
# (4 × 2.0 × 4 HIGH dimensions) + (4 × 1.5 × 5 MEDIUM dimensions) + (4 × 1.0 × 5 LOW dimensions)
# = 32 + 30 + 20 = 82
MAX_SECTION_A_WEIGHTED_SCORE = 82.0

# Section B weights (v2 — no ROUGE)
SECTION_B_WEIGHTS = {
    "M1_agent_output_coverage": 0.20,
    "M2_report_completeness": 0.20,
    "M3_citation_groundedness": 0.25,
    "M4_evidence_breadth": 0.15,
    "M5_semantic_groundedness": 0.20,
}

RESEARCH_AGENT_OUTPUT_KEYS = {
    k: v for k, v in AGENT_OUTPUT_KEYS.items() if v != "final_report"
}

# Expected minimum unique domains for M7
MIN_EXPECTED_DOMAINS = 8

# Expected report sections for M6 completeness (13 total per report structure)
EXPECTED_SECTION_COUNT = 13


class EvaluationService:
    """
    Orchestrates Section A (LLM-as-judge) and Section B (automated) evaluation
    of a generated sales intelligence report.
    """

    def __init__(self):
        self._catalog_context: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        request_id: str,
        final_report: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Run the full evaluation pipeline and return a structured dict
        suitable for storage as evaluation.json.
        """
        logger.info(f"[Evaluation] Starting evaluation for request {request_id}")

        job_evidence = session_state.get("job_evidence")
        if not isinstance(job_evidence, list) or not job_evidence:
            job_evidence = aggregate_job_evidence(session_state)

        logger.info(
            f"[Evaluation] job_evidence entries: {len(job_evidence)}"
        )

        # ------------------------------------------------------------------
        # Section A: LLM-as-judge
        # ------------------------------------------------------------------
        section_a_result = await self._run_section_a(
            final_report, session_state, job_evidence
        )

        # ------------------------------------------------------------------
        # Section B: Automated metrics
        # ------------------------------------------------------------------
        section_b_result = await self._run_section_b(
            final_report, session_state, job_evidence
        )

        # ------------------------------------------------------------------
        # Final composite score
        # ------------------------------------------------------------------
        section_a_score = section_a_result.get("section_a_score", 0.0)
        section_b_score = section_b_result.get("section_b_score", 0.0)
        # Penalties already deducted inside section_a_score
        final_score = (section_a_score * 0.80) + (section_b_score * 0.20)

        evaluation_result = {
            "section_a": section_a_result,
            "section_b": section_b_result,
            "final_composite_score": round(final_score, 2),
            "evaluation_metadata": {
                "evaluator_model": settings.EVALUATOR_MODEL,
                "evaluated_at": datetime.now(UTC).isoformat(),
                "request_id": request_id,
                "scoring_version": "v2",
                "job_evidence_count": len(job_evidence),
            },
        }

        logger.info(
            f"[Evaluation] Completed for request {request_id} — "
            f"Final Score: {final_score:.2f}"
        )
        return evaluation_result

    # ------------------------------------------------------------------
    # Section A: LLM-as-judge
    # ------------------------------------------------------------------

    async def _run_section_a(
        self,
        final_report: str,
        session_state: dict[str, Any],
        job_evidence: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Call the LLM judge and compute Section A score."""
        try:
            # Dynamically fetch relevant catalog context using Vector Search
            catalog_context = await self._fetch_relevant_catalog_context(final_report)
            raw_llm_response = await self._call_llm_judge(
                final_report,
                session_state,
                catalog_context,
                job_evidence=job_evidence or [],
            )
            return self._parse_and_score_section_a(raw_llm_response)
        except Exception as e:
            logger.error(f"[Evaluation] Section A failed: {e}")
            return self._empty_section_a(error=str(e))

    async def _fetch_relevant_catalog_context(self, report: str) -> str:
        """
        Extract key technical needs from the report and perform a Vector Search
        to get the most relevant catalog context for the judge.
        """
        if self._catalog_context:
            return self._catalog_context

        try:
            # Extract keywords from the Technology Alignment section
            alignment_section = self._extract_alignment_section(report)
            search_query = (
                alignment_section[:500]
                if alignment_section
                else "Colt product solutions"
            )

            # Perform Vector Search
            logger.info(
                f"[Evaluation] Fetching catalog context for query: {search_query[:50]}..."
            )
            self._catalog_context = await asyncio.to_thread(
                colt_product_search, search_query
            )
            return self._catalog_context
        except Exception as e:
            logger.warning(f"[Evaluation] Vector Search for catalog failed: {e}")
            return ""

    def _extract_alignment_section(self, report: str) -> str:
        """Extract the text of the Colt Technology Alignment section."""
        patterns = [
            r"##\s*8\.?\s*Colt\s+Technology\s+Alignment(.*?)(?=\n##|\Z)",
            r"##\s*Colt\s+Technology\s+Alignment(.*?)(?=\n##|\Z)",
        ]
        for pattern in patterns:
            match = re.search(pattern, report, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return ""

    async def _call_llm_judge(
        self,
        final_report: str,
        session_state: dict[str, Any],
        catalog_context: str,
        job_evidence: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Send the evaluation prompt to the configured LLM judge via Google Gen AI
        and parse the JSON response.
        """
        from google.genai import types as genai_types

        client = get_genai_client()

        prompt = self._build_judge_prompt(
            final_report,
            session_state,
            catalog_context,
            job_evidence=job_evidence or [],
        )

        response = client.models.generate_content(
            model=settings.EVALUATOR_MODEL,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

        raw_text = response.text.strip() if response.text else ""
        # Strip markdown fences if present
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\n?", "", raw_text)
            raw_text = re.sub(r"\n?```$", "", raw_text)

        return json.loads(raw_text)

    def _build_judge_prompt(
        self,
        final_report: str,
        session_state: dict[str, Any],
        catalog_context: str,
        job_evidence: list[dict] | None = None,
    ) -> str:
        """Construct the detailed scoring prompt for the LLM judge."""

        agent_outputs_block = format_agent_outputs_for_judge(
            session_state, RESEARCH_AGENT_OUTPUT_KEYS
        )

        verifications = {
            k: v
            for k, v in session_state.items()
            if k.endswith("_verification_result")
            or k.endswith("_bm25_status")
            or k.startswith("verification_")
        }
        verifications_text = json.dumps(verifications, indent=2)

        dimension_rubric = ""
        for dim_key, cfg in DIMENSION_CONFIG.items():
            # Add explicit section mapping to guide the judge
            mapping_hint = ""
            if dim_key == "D6_colt_solution_alignment_table":
                mapping_hint = " (Found in '## 8. Colt Technology Alignment Table')"
            elif dim_key == "D14_why_colt_why_now_summary":
                mapping_hint = (
                    " (Found in '## 11. Strategic Opportunity & Live Call Readiness')"
                )
            elif dim_key == "D13_signals_growth_risk_campaign":
                mapping_hint = " (Found in '## 12. Signals')"

            dimension_rubric += textwrap.dedent(f"""
            **{dim_key}** (Weight: ×{cfg["weight"]}) — {cfg["category"]}{mapping_hint}
            {cfg["description"]}
            - Score 0: Completely absent (Check the headers listed above before choosing this!)
            - Score 1: Present but superficial / largely inaccurate
            - Score 2: Present with moderate depth / some inaccuracies
            - Score 3: Well-researched, mostly accurate, commercially relevant
            - Score 4: Exceptional — specific, current, commercially actionable
            """)

        evidence_section = ""
        if job_evidence:
            evidence_block = evidence_to_block(job_evidence, max_chars=8000)
            if evidence_block:
                evidence_section = textwrap.dedent(f"""
        ## VERIFIED EVIDENCE (job_evidence — web snippets gathered during this research job):
        Use this as the authoritative source of truth when assessing M12 hallucinations.
        A numerical fact (revenue, headcount, growth rate, fine amount) is a hallucination
        if it does not appear in this evidence and cannot be inferred from it.
        {evidence_block}
        """)

        prompt = textwrap.dedent(f"""
        You are a senior sales intelligence evaluator at Colt Technology Services.
        Your task is to evaluate a generated company research report based on 14 quality dimensions (D1–D14)
        and flag any hallucinations (M12) or policy violations (M13).

        ## COLT PRODUCT CATALOG CONTEXT (Use this to assess alignment accuracy):
        {catalog_context[:8000]}
        {evidence_section}

        ## REAL-TIME VERIFICATION RESULTS (Fact-checks performed during this run):
        These results come from a secondary search agent that verified claims as they were gathered.
        TRUST these results over your internal training data.
        {verifications_text}

        ## REPORT TO EVALUATE:
        {final_report}

        ## AGENT OUTPUTS (structured — compare report sections to these):
        {agent_outputs_block}

        ## SCORING RUBRIC (score each 0–4):
        {dimension_rubric}

        ## PENALTY METRICS:
        - **M12_hallucination_count**: Count of factual claims in the report that cannot be
          verified against the VERIFIED EVIDENCE or REAL-TIME VERIFICATION RESULTS above.
          **CRITICAL GUIDELINES FOR M12:**
          - Ground M12 only on VERIFIED EVIDENCE (job_evidence) and REAL-TIME VERIFICATION RESULTS.
            Do not use internal training data to contradict report figures.
          - If a claim is supported by verification results or appears in job_evidence snippets,
            do NOT flag as hallucination.
          - Only flag claims that are clearly fabricated or contradicted by the evidence above.
          - **Colt product descriptions**: Never flag Colt's own product/service descriptions
            as hallucinations.
        - **M13_policy_violation_count**: Count of statements that violate content policy
          (e.g., personal data exposure, discriminatory content, misleading claims). Each violation = 1.

        ## SELF-CORRECTION PROTOCOL (READ BEFORE FINISHING):
        1. **MANDATORY SCAN**: Before you declare a section 'absent' or assign a Score 0, perform a text search in the 'REPORT TO EVALUATE' for the corresponding header (e.g., '## 8.' or '## 11.').
        2. **NO TRUNCATION ASSUMPTION**: Do not assume the report ends early. Scroll through the entirety of the provided text.
        3. **PLURALITY**: If a metric asks for 'Signals' and you see a '## 12. Signals' header, it is NOT absent.

        ## OUTPUT INSTRUCTIONS:
        Return ONLY a valid JSON object (no markdown, no explanation) with this exact structure:
        {{
          "D1_data_currency_recency": <integer 0-4>,
          "D2_executive_intelligence_bios": <integer 0-4>,
          "D3_cybersecurity_context": <integer 0-4>,
          "D4_strategic_priorities_alignment": <integer 0-4>,
          "D5_technology_landscape_detail": <integer 0-4>,
          "D6_colt_solution_alignment_table": <integer 0-4>,
          "D7_procurement_buying_signals": <integer 0-4>,
          "D8_financial_trading_relevance": <integer 0-4>,
          "D9_global_operations_footprint": <integer 0-4>,
          "D10_regulatory_compliance_detail": <integer 0-4>,
          "D11_relationship_ecosystem_mapping": <integer 0-4>,
          "D12_sustainability_esg_alignment": <integer 0-4>,
          "D13_signals_growth_risk_campaign": <integer 0-4>,
          "D14_why_colt_why_now_summary": <integer 0-4>,
          "M12_hallucination_count": <integer >= 0>,
          "M13_policy_violation_count": <integer >= 0>,
          "scoring_rationale": {{
            "D1_data_currency_recency": "<brief 1-sentence rationale>",
            "D2_executive_intelligence_bios": "<brief 1-sentence rationale>",
            "D3_cybersecurity_context": "<brief 1-sentence rationale>",
            "D4_strategic_priorities_alignment": "<brief 1-sentence rationale>",
            "D5_technology_landscape_detail": "<brief 1-sentence rationale>",
            "D6_colt_solution_alignment_table": "<brief 1-sentence rationale>",
            "D7_procurement_buying_signals": "<brief 1-sentence rationale>",
            "D8_financial_trading_relevance": "<brief 1-sentence rationale>",
            "D9_global_operations_footprint": "<brief 1-sentence rationale>",
            "D10_regulatory_compliance_detail": "<brief 1-sentence rationale>",
            "D11_relationship_ecosystem_mapping": "<brief 1-sentence rationale>",
            "D12_sustainability_esg_alignment": "<brief 1-sentence rationale>",
            "D13_signals_growth_risk_campaign": "<brief 1-sentence rationale>",
            "D14_why_colt_why_now_summary": "<brief 1-sentence rationale>",
            "M12_hallucination_count": "<brief explanation of any hallucinations found>",
            "M13_policy_violation_count": "<brief explanation of any violations found>"
          }}
        }}
        """)

        return prompt

    def _parse_and_score_section_a(self, llm_output: dict[str, Any]) -> dict[str, Any]:
        """Parse LLM judge output and compute Section A composite score."""
        dimension_results = {}
        total_weighted = 0.0

        for dim_key, cfg in DIMENSION_CONFIG.items():
            raw_score = llm_output.get(dim_key, 0)
            # Clamp between 0 and 4
            raw_score = max(0, min(4, int(raw_score)))
            weighted = raw_score * cfg["weight"]
            total_weighted += weighted
            dimension_results[dim_key] = {
                "score": raw_score,
                "weight": cfg["weight"],
                "weighted_score": round(weighted, 2),
                "category": cfg["category"],
                "rationale": llm_output.get("scoring_rationale", {}).get(dim_key, ""),
            }

        # Penalty counts
        m12_count = max(0, int(llm_output.get("M12_hallucination_count", 0)))
        m13_count = max(0, int(llm_output.get("M13_policy_violation_count", 0)))

        # Penalties
        penalty_deduction = (m12_count * 10) + (m13_count * 15)

        # Section A composite
        section_a_percentage = (total_weighted / MAX_SECTION_A_WEIGHTED_SCORE) * 100
        section_a_score = max(0.0, section_a_percentage - penalty_deduction)

        return {
            "dimensions": dimension_results,
            "M12_hallucination_count": m12_count,
            "M12_penalty_points": m12_count * 10,
            "M12_rationale": llm_output.get("scoring_rationale", {}).get(
                "M12_hallucination_count", ""
            ),
            "M13_policy_violation_count": m13_count,
            "M13_penalty_points": m13_count * 15,
            "M13_rationale": llm_output.get("scoring_rationale", {}).get(
                "M13_policy_violation_count", ""
            ),
            "section_a_raw_weighted": round(total_weighted, 2),
            "section_a_max_weighted": MAX_SECTION_A_WEIGHTED_SCORE,
            "section_a_percentage": round(section_a_percentage, 2),
            "total_penalty_deduction": penalty_deduction,
            "section_a_score": round(section_a_score, 2),
            "section_a_weight": 0.80,
        }

    def _empty_section_a(self, error: str = "") -> dict[str, Any]:
        """Return a zeroed Section A result in case of LLM failure."""
        dimension_results = {
            dim_key: {
                "score": 0,
                "weight": cfg["weight"],
                "weighted_score": 0.0,
                "category": cfg["category"],
                "rationale": "Evaluation failed",
            }
            for dim_key, cfg in DIMENSION_CONFIG.items()
        }
        return {
            "dimensions": dimension_results,
            "M12_hallucination_count": 0,
            "M12_penalty_points": 0,
            "M12_rationale": "",
            "M13_policy_violation_count": 0,
            "M13_penalty_points": 0,
            "M13_rationale": "",
            "section_a_raw_weighted": 0.0,
            "section_a_max_weighted": MAX_SECTION_A_WEIGHTED_SCORE,
            "section_a_percentage": 0.0,
            "total_penalty_deduction": 0,
            "section_a_score": 0.0,
            "section_a_weight": 0.80,
            "error": error,
        }

    # ------------------------------------------------------------------
    # Section B: Automated metrics
    # ------------------------------------------------------------------

    async def _run_section_b(
        self,
        final_report: str,
        session_state: dict[str, Any],
        job_evidence: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Compute all automated metrics for Section B (v2)."""
        evidence = job_evidence or []

        try:
            m1 = self._compute_agent_output_coverage(session_state)
        except Exception as e:
            logger.warning(f"[Evaluation] Agent coverage failed: {e}")
            m1 = 0.0

        try:
            m2 = self._compute_completeness(final_report)
        except Exception as e:
            logger.warning(f"[Evaluation] Completeness computation failed: {e}")
            m2 = 0.0

        try:
            m3 = self._compute_groundedness(final_report, job_evidence=evidence)
        except Exception as e:
            logger.warning(f"[Evaluation] Citation groundedness failed: {e}")
            m3 = 0.0

        try:
            m4 = self._compute_evidence_breadth(job_evidence=evidence)
        except Exception as e:
            logger.warning(f"[Evaluation] Evidence breadth failed: {e}")
            m4 = 0.0

        try:
            m5 = await asyncio.to_thread(
                compute_semantic_groundedness, final_report, evidence
            )
        except Exception as e:
            logger.warning(f"[Evaluation] Semantic groundedness failed: {e}")
            m5 = 0.0

        m1 = round(m1, 4)
        m2 = round(m2, 4)
        m3 = round(m3, 4)
        m4 = round(m4, 4)
        m5 = round(m5, 4)

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

    def _compute_agent_output_coverage(self, session_state: dict[str, Any]) -> float:
        """Fraction of research/signals agent outputs that are non-empty."""
        total = len(RESEARCH_AGENT_OUTPUT_KEYS)
        if total == 0:
            return 0.0
        populated = sum(
            1
            for key in RESEARCH_AGENT_OUTPUT_KEYS.values()
            if session_state.get(key) and str(session_state.get(key)).strip()
        )
        return populated / total

    def _compute_evidence_breadth(self, job_evidence: list[dict]) -> float:
        """Unique domains in job_evidence / MIN_EXPECTED_DOMAINS."""
        urls = [e.get("url", "") for e in job_evidence if e.get("url", "").strip()]
        unique_domains = self._count_unique_domains(urls)
        return min(1.0, unique_domains / MIN_EXPECTED_DOMAINS)

    def _compute_groundedness(
        self,
        final_report: str,
        job_evidence: list[dict] | None = None,
    ) -> float:
        """
        Groundedness measures how well the cited sources in Section 13 correspond to
        sources that were actually scraped during the research job.

        Citation groundedness: cited Section 13 domains appearing in job_evidence.
        Capped at 1.0.
        """
        section_13 = self._extract_section_13(final_report)
        if not section_13:
            logger.warning(
                "[Evaluation] Section 13 (Source Summary) not found in report"
            )
            return 0.0

        cited_urls = self._extract_urls(section_13)
        evidence = job_evidence or []

        if evidence:
            cached_domains = set()
            for e in evidence:
                url = (e.get("url") or "").strip().lower()
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
                f"in job_evidence ({len(cached_domains)} domains) → {score:.3f}"
            )
        else:
            unique_domains = self._count_unique_domains(cited_urls)
            score = min(1.0, unique_domains / MIN_EXPECTED_DOMAINS)
            logger.debug(
                f"[Evaluation] Citation groundedness (no evidence): "
                f"{unique_domains} domains in Section 13 → {score:.3f}"
            )

        return score

    def _compute_completeness(self, final_report: str) -> float:
        """
        Completeness = fraction of 13 expected sections that are populated
        (i.e., not marked as 'publicly unavailable' or empty).
        """
        # The 13 expected section headers per report structure
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
                # Check if the section after the header is non-trivial
                idx = report_lower.find(header.lower())
                section_slice = final_report[idx : idx + 500].lower()
                if (
                    "publicly unavailable" not in section_slice
                    or len(section_slice) > 200
                ):
                    populated += 1

        completeness = populated / EXPECTED_SECTION_COUNT
        logger.debug(
            f"[Evaluation] Completeness: {populated}/{EXPECTED_SECTION_COUNT} sections populated → {completeness:.3f}"
        )
        return completeness

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_section_13(self, report: str) -> str:
        """Extract the text of Section 13 (Source Summary) from the report."""
        # Try various patterns the report compiler might use
        patterns = [
            r"##\s*13\.?\s*Source\s+Summary(.*?)(?=\n##|\Z)",
            r"##\s*Source\s+Summary(.*?)(?=\n##|\Z)",
        ]
        for pattern in patterns:
            match = re.search(pattern, report, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1).strip()
        return ""

    def _extract_urls(self, text: str) -> list[str]:
        """Extract all HTTP/HTTPS URLs from text."""
        url_pattern = r"https?://[^\s\)\]\,\"\'\<\>]+"
        return re.findall(url_pattern, text)

    def _count_unique_domains(self, urls: list[str]) -> int:
        """Count unique netloc (domain) values from a list of URLs."""
        domains = set()
        for url in urls:
            try:
                parsed = urlparse(url)
                if parsed.netloc:
                    # Normalise: strip www. prefix for deduplication
                    domain = parsed.netloc.lower().removeprefix("www.")
                    domains.add(domain)
            except Exception:
                pass
        return len(domains)

