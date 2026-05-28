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

from ....core.config import settings
from ....core.logging_config import logger
from ....dependencies.service_dependencies import get_genai_client
from ...catalog.search import colt_product_search
from .evaluation_config import (
    DIMENSION_CONFIG,
    RESEARCH_AGENT_OUTPUT_KEYS,
)
from .evaluation_section_a import empty_section_a, parse_and_score_section_a
from .evaluation_section_b import (
    build_section_b_result,
    compute_agent_output_coverage,
    compute_completeness,
    compute_evidence_breadth,
    compute_groundedness,
)
from .sales.utils.embedding_similarity import compute_semantic_groundedness
from .sales.utils.evidence import (
    aggregate_job_evidence,
    evidence_to_block,
    format_agent_outputs_for_judge,
)


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
        return parse_and_score_section_a(llm_output)

    def _empty_section_a(self, error: str = "") -> dict[str, Any]:
        return empty_section_a(error)

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
            m1 = compute_agent_output_coverage(session_state)
        except Exception as e:
            logger.warning(f"[Evaluation] Agent coverage failed: {e}")
            m1 = 0.0

        try:
            m2 = compute_completeness(final_report)
        except Exception as e:
            logger.warning(f"[Evaluation] Completeness computation failed: {e}")
            m2 = 0.0

        try:
            m3 = compute_groundedness(final_report, job_evidence=evidence)
        except Exception as e:
            logger.warning(f"[Evaluation] Citation groundedness failed: {e}")
            m3 = 0.0

        try:
            m4 = compute_evidence_breadth(job_evidence=evidence)
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
        return build_section_b_result(m1=m1, m2=m2, m3=m3, m4=m4, m5=m5)

