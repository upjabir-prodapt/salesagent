"""Evaluation Service - automated and LLM-based report quality evaluation."""

from __future__ import annotations

import asyncio
import json
import re
import textwrap
from datetime import UTC, datetime
from typing import Any

from src.shared.config import settings
from src.shared.logging_config import logger
from src.shared.repositories.clients import get_genai_client

from ..agents.tools.evidence import (
    aggregate_job_evidence,
    evidence_to_block,
    format_agent_outputs_for_judge,
)
from ..agents.tools.gcs_pdf_loader import get_alignment_context
from ..agents.tools.verification import compute_semantic_groundedness
from ..runtime.pricing import record_genai_response_usage
from .config import DIMENSION_CONFIG, RESEARCH_AGENT_OUTPUT_KEYS
from .section_a import empty_section_a, parse_and_score_section_a
from .section_b import (
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
        m1 = compute_agent_output_coverage(session_state)
        m2 = compute_completeness(final_report)
        m3 = compute_groundedness(final_report, evidence)
        m4 = compute_evidence_breadth(evidence)

        m5 = 1.0
        if settings.EVAL_EMBEDDING_ENABLED:
            try:
                m5 = await asyncio.to_thread(
                    compute_semantic_groundedness,
                    final_report,
                    evidence,
                    sections=("11.", "8."),
                )
            except Exception as e:
                logger.warning(
                    f"[Evaluation] Semantic groundedness computation failed: {e}"
                )
                m5 = 0.5
        else:
            logger.debug(
                "[Evaluation] Semantic groundedness disabled by config (M5=1.0)"
            )

        return build_section_b_result(m1=m1, m2=m2, m3=m3, m4=m4, m5=m5)

    async def _fetch_relevant_catalog_context(self, final_report: str) -> str:
        if self._catalog_context:
            return self._catalog_context

        try:
            company_name = "default"
            context = get_alignment_context(company_name)
            self._catalog_context = context[:8000]
            return self._catalog_context
        except Exception as e:
            logger.warning(
                f"[Evaluation] Failed to fetch catalog context: {e}; using fallback"
            )
            return "Colt Technology Services provides high-bandwidth network and cloud connectivity."

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
        record_genai_response_usage(session_state, settings.EVALUATOR_MODEL, response)
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
                evidence_section = f"## VERIFIED EVIDENCE:\n{evidence_block}\n"

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


__all__ = ["EvaluationService"]
