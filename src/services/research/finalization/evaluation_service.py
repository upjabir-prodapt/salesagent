"""Evaluation Service - automated and LLM-based report quality evaluation."""

from __future__ import annotations

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
from ..agents.sales.tools.evidence import (
    aggregate_job_evidence,
    evidence_to_block,
    format_agent_outputs_for_judge,
)
from ..agents.sales.tools.verification import compute_semantic_groundedness
from .evaluation_config import DIMENSION_CONFIG, RESEARCH_AGENT_OUTPUT_KEYS
from .evaluation_section_a import empty_section_a, parse_and_score_section_a
from .evaluation_section_b import (
    build_section_b_result,
    compute_agent_output_coverage,
    compute_completeness,
    compute_evidence_breadth,
    compute_groundedness,
)


class EvaluationService:
    """Orchestrates Section A (LLM judge) and Section B (automated) scoring."""

    def __init__(self):
        self._catalog_context: str | None = None

    async def evaluate(
        self,
        request_id: str,
        final_report: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        logger.info(f"[Evaluation] Starting evaluation for request {request_id}")

        job_evidence = session_state.get("job_evidence")
        if not isinstance(job_evidence, list) or not job_evidence:
            job_evidence = aggregate_job_evidence(session_state)

        section_a_result = await self._run_section_a(
            final_report, session_state, job_evidence
        )
        section_b_result = await self._run_section_b(
            final_report, session_state, job_evidence
        )

        section_a_score = section_a_result.get("section_a_score", 0.0)
        section_b_score = section_b_result.get("section_b_score", 0.0)
        final_score = (section_a_score * 0.80) + (section_b_score * 0.20)

        return {
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

    async def _run_section_a(
        self,
        final_report: str,
        session_state: dict[str, Any],
        job_evidence: list[dict] | None = None,
    ) -> dict[str, Any]:
        try:
            catalog_context = await self._fetch_relevant_catalog_context(final_report)
            raw_llm_response = await self._call_llm_judge(
                final_report,
                session_state,
                catalog_context,
                job_evidence=job_evidence or [],
            )
            return parse_and_score_section_a(raw_llm_response)
        except Exception as e:
            logger.error(f"[Evaluation] Section A failed: {e}")
            return empty_section_a(error=str(e))

    async def _run_section_b(
        self,
        final_report: str,
        session_state: dict[str, Any],
        job_evidence: list[dict] | None = None,
    ) -> dict[str, Any]:
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

        return build_section_b_result(
            m1=round(m1, 4),
            m2=round(m2, 4),
            m3=round(m3, 4),
            m4=round(m4, 4),
            m5=round(m5, 4),
        )

    async def _fetch_relevant_catalog_context(self, report: str) -> str:
        if self._catalog_context:
            return self._catalog_context
        try:
            alignment_section = self._extract_alignment_section(report)
            search_query = (
                alignment_section[:500]
                if alignment_section
                else "Colt product solutions"
            )
            self._catalog_context = await asyncio.to_thread(
                colt_product_search, search_query
            )
            return self._catalog_context
        except Exception as e:
            logger.warning(f"[Evaluation] Vector Search for catalog failed: {e}")
            return ""

    @staticmethod
    def _extract_alignment_section(report: str) -> str:
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
            dimension_rubric += textwrap.dedent(
                f"""
                **{dim_key}** (Weight: x{cfg["weight"]}) - {cfg["category"]}
                {cfg["description"]}
                - Score 0: absent
                - Score 1: weak
                - Score 2: moderate
                - Score 3: strong
                - Score 4: exceptional
                """
            )

        evidence_section = ""
        if job_evidence:
            evidence_block = evidence_to_block(job_evidence, max_chars=6000)
            if evidence_block:
                evidence_section = (
                    "## VERIFIED EVIDENCE:\n"
                    f"{evidence_block}\n"
                )

        return textwrap.dedent(
            f"""
            You are a senior sales intelligence evaluator at Colt Technology Services.

            ## COLT PRODUCT CATALOG CONTEXT:
            {catalog_context[:8000]}

            {evidence_section}

            ## VERIFICATION RESULTS:
            {verifications_text}

            ## REPORT TO EVALUATE:
            {final_report}

            ## AGENT OUTPUTS:
            {agent_outputs_block}

            ## RUBRIC:
            {dimension_rubric}

            Return ONLY a valid JSON object containing all D1-D14 and M12/M13 keys,
            plus a scoring_rationale object.
            """
        )
