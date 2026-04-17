"""
Evaluation Service - Automated and LLM-based Report Quality Evaluation

Two-section evaluation framework:
  Section A (80%): LLM-as-judge scoring 14 human dimensions (D1-D14) with weights,
                   plus binary penalties for hallucinations (M12) and policy violations (M13).
  Section B (20%): Automated heuristic metrics: ROUGE-1/2/L, BERTScore, Groundedness,
                   Completeness, Source Diversity.

Results are stored as evaluation.json in GCS alongside raw_data.json and final_report.md.
"""

import json
import re
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from ..core.config import settings

# ---------------------------------------------------------------------------
# Dimension weights for Section A (D1–D14)
# ---------------------------------------------------------------------------
DIMENSION_CONFIG = {
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

# Section B weights
SECTION_B_WEIGHTS = {
    "M1_rouge1": 0.15,
    "M2_rouge2": 0.10,
    "M3_rougel": 0.10,
    "M4_bertscore": 0.30,
    "M5_groundedness": 0.15,
    "M6_completeness": 0.10,
    "M7_source_diversity": 0.10,
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
        self._catalog_text: str | None = None
        self._catalog_loaded: bool = False

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

        # Extract raw search cache — the ground-truth evidence for all metrics
        raw_search_cache: list[dict] = session_state.get("raw_search_cache") or []
        logger.info(
            f"[Evaluation] raw_search_cache entries available: {len(raw_search_cache)}"
        )

        # Build reference text for automated metrics.
        # Prefer the raw search cache (actual scraped web content) over the LLM-generated
        # agent outputs so that ROUGE/BERTScore measure fidelity to real evidence rather
        # than self-consistency between LLM outputs.
        if raw_search_cache:
            reference_text = self._cache_to_text(raw_search_cache)
            logger.info(
                f"[Evaluation] Using raw_search_cache as reference text "
                f"({len(reference_text)} chars)"
            )
        else:
            reference_text = self._session_state_to_text(session_state)
            logger.warning(
                "[Evaluation] raw_search_cache empty — falling back to session_state as reference"
            )

        # ------------------------------------------------------------------
        # Section A: LLM-as-judge
        # ------------------------------------------------------------------
        section_a_result = await self._run_section_a(
            final_report, session_state, raw_search_cache
        )

        # ------------------------------------------------------------------
        # Section B: Automated metrics
        # ------------------------------------------------------------------
        section_b_result = self._run_section_b(
            final_report, reference_text, raw_search_cache
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
                "bertscore_model": settings.BERTSCORE_MODEL,
                "evaluated_at": datetime.now(UTC).isoformat(),
                "request_id": request_id,
            },
        }

        logger.success(
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
        raw_search_cache: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Call the LLM judge and compute Section A score."""
        try:
            catalog_context = self._load_catalog_text()
            raw_llm_response = await self._call_llm_judge(
                final_report,
                session_state,
                catalog_context,
                raw_search_cache=raw_search_cache or [],
            )
            return self._parse_and_score_section_a(raw_llm_response)
        except Exception as e:
            logger.error(f"[Evaluation] Section A failed: {e}")
            return self._empty_section_a(error=str(e))

    async def _call_llm_judge(
        self,
        final_report: str,
        session_state: dict[str, Any],
        catalog_context: str,
        raw_search_cache: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Send the evaluation prompt to the configured LLM judge via Vertex AI
        and parse the JSON response.
        """
        # Import here to avoid circular deps at module level
        import vertexai
        from vertexai.generative_models import GenerationConfig, GenerativeModel

        vertexai.init(
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )

        prompt = self._build_judge_prompt(
            final_report,
            session_state,
            catalog_context,
            raw_search_cache=raw_search_cache or [],
        )

        model = GenerativeModel(settings.EVALUATOR_MODEL)
        response = model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

        raw_text = response.text.strip()
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
        raw_search_cache: list[dict] | None = None,
    ) -> str:
        """Construct the detailed scoring prompt for the LLM judge."""

        # Extract key session state sections for context
        firmographics = json.dumps(
            session_state.get("firmographicsagent_output", {}), indent=2
        )
        geographic = json.dumps(
            session_state.get("geographicagent_output", {}), indent=2
        )
        alignment_output = json.dumps(
            session_state.get("alignment_output", {}), indent=2
        )
        tech_stack = json.dumps(
            session_state.get("techstackagent_output", {}), indent=2
        )
        strategy = json.dumps(session_state.get("strategyagent_output", {}), indent=2)
        executive = json.dumps(
            session_state.get("executivepipeline_output", {}), indent=2
        )
        compliance = json.dumps(
            session_state.get("complianceagent_output", {}), indent=2
        )
        procurement = json.dumps(
            session_state.get("procurementagent_output", {}), indent=2
        )
        market = json.dumps(session_state.get("marketagent_output", {}), indent=2)
        ecosystem = json.dumps(session_state.get("ecosystemagent_output", {}), indent=2)
        signals_growth = json.dumps(
            session_state.get("growthsignals_output", {}), indent=2
        )
        signals_risk = json.dumps(session_state.get("risksignals_output", {}), indent=2)
        signals_campaign = json.dumps(
            session_state.get("campaignsignals_output", {}), indent=2
        )

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

        # Build verified evidence block from raw search cache (capped at 8 000 chars
        # to stay within the judge model's context budget alongside the report and catalog)
        evidence_section = ""
        if raw_search_cache:
            evidence_block = self._cache_to_evidence_block(
                raw_search_cache, max_chars=8000
            )
            if evidence_block:
                evidence_section = textwrap.dedent(f"""
        ## VERIFIED EVIDENCE (raw web-scraped snippets gathered during this research job):
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
        ## REPORT TO EVALUATE:
        {final_report}

        ## SUPPORTING RESEARCH DATA (EXCERPTS):
        ### Firmographics & Snapshot:
        {firmographics[:3000]}

        ### Geographic Operations:
        {geographic[:3000]}

        ### Strategy & Challenges:
        {strategy[:5000]}

        ### Executive Intelligence:
        {executive[:4000]}

        ### Technology Landscape:
        {tech_stack[:4000]}

        ### Compliance & Regulatory:
        {compliance[:3000]}

        ### Procurement Patterns:
        {procurement[:3000]}

        ### Market Position:
        {market[:4000]}

        ### Ecosystem & Partnerships:
        {ecosystem[:3000]}

        ### Colt Alignment Output:
        {alignment_output[:5000]}

        ### Signals (Growth / Risk / Campaign):
        Growth: {signals_growth[:800]}
        Risk: {signals_risk[:800]}
        Campaign: {signals_campaign[:800]}

        ## SCORING RUBRIC (score each 0–4):
        {dimension_rubric}

        ## PENALTY METRICS:
        - **M12_hallucination_count**: Count of factual claims in the report that cannot be
          verified against the VERIFIED EVIDENCE section above, or that contradict it.
          **CRITICAL GUIDELINES FOR M12:**
          - **Use the VERIFIED EVIDENCE as the source of truth**: If the VERIFIED EVIDENCE section
            is present, a numerical claim (revenue, headcount, growth rate, fine, contract value)
            is a hallucination if it does not appear in that evidence. Do NOT apply leniency for
            "close enough" figures — the evidence contains what was actually scraped.
          - **No VERIFIED EVIDENCE present**: Apply financial and percentage leniency (small
            discrepancies like $57.8B vs $58.7B are NOT hallucinations); only flag clear
            fabrications such as a named executive that does not exist in the research data or
            a claim that contradicts the core nature of the business.
          - **Colt product descriptions**: Never flag Colt's own product/service descriptions
            as hallucinations — these are not verifiable from scraped company evidence.
          - **Only flag clear fabrications** when no evidence is provided.
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

    def _run_section_b(
        self,
        final_report: str,
        reference_text: str,
        raw_search_cache: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Compute all automated metrics for Section B."""
        try:
            rouge_scores = self._compute_rouge(final_report, reference_text)
        except Exception as e:
            logger.warning(f"[Evaluation] ROUGE computation failed: {e}")
            rouge_scores = {"rouge1": 0.0, "rouge2": 0.0, "rougeLsum": 0.0}

        try:
            bert_f1 = self._compute_bertscore(final_report, reference_text)
        except Exception as e:
            logger.warning(f"[Evaluation] BERTScore computation failed: {e}")
            bert_f1 = 0.0

        try:
            groundedness = self._compute_groundedness(
                final_report, raw_search_cache=raw_search_cache or []
            )
        except Exception as e:
            logger.warning(f"[Evaluation] Groundedness computation failed: {e}")
            groundedness = 0.0

        try:
            completeness = self._compute_completeness(final_report)
        except Exception as e:
            logger.warning(f"[Evaluation] Completeness computation failed: {e}")
            completeness = 0.0

        try:
            source_diversity = self._compute_source_diversity(
                final_report, raw_search_cache=raw_search_cache or []
            )
        except Exception as e:
            logger.warning(f"[Evaluation] Source Diversity computation failed: {e}")
            source_diversity = 0.0

        m1 = round(rouge_scores.get("rouge1", 0.0), 4)
        m2 = round(rouge_scores.get("rouge2", 0.0), 4)
        m3 = round(rouge_scores.get("rougeLsum", 0.0), 4)
        m4 = round(bert_f1, 4)
        m5 = round(groundedness, 4)
        m6 = round(completeness, 4)
        m7 = round(source_diversity, 4)

        # Weighted average × 100 to put on 0–100 scale
        section_b_score = (
            m1 * SECTION_B_WEIGHTS["M1_rouge1"]
            + m2 * SECTION_B_WEIGHTS["M2_rouge2"]
            + m3 * SECTION_B_WEIGHTS["M3_rougel"]
            + m4 * SECTION_B_WEIGHTS["M4_bertscore"]
            + m5 * SECTION_B_WEIGHTS["M5_groundedness"]
            + m6 * SECTION_B_WEIGHTS["M6_completeness"]
            + m7 * SECTION_B_WEIGHTS["M7_source_diversity"]
        ) * 100

        return {
            "M1_rouge1": m1,
            "M1_rouge1_weight": SECTION_B_WEIGHTS["M1_rouge1"],
            "M2_rouge2": m2,
            "M2_rouge2_weight": SECTION_B_WEIGHTS["M2_rouge2"],
            "M3_rougel": m3,
            "M3_rougel_weight": SECTION_B_WEIGHTS["M3_rougel"],
            "M4_bertscore": m4,
            "M4_bertscore_model": settings.BERTSCORE_MODEL,
            "M4_bertscore_weight": SECTION_B_WEIGHTS["M4_bertscore"],
            "M5_groundedness": m5,
            "M5_groundedness_weight": SECTION_B_WEIGHTS["M5_groundedness"],
            "M5_groundedness_method": "URL count in Section 13 Source Summary / total URLs expected",
            "M6_completeness": m6,
            "M6_completeness_weight": SECTION_B_WEIGHTS["M6_completeness"],
            "M6_sections_expected": EXPECTED_SECTION_COUNT,
            "M7_source_diversity": m7,
            "M7_source_diversity_weight": SECTION_B_WEIGHTS["M7_source_diversity"],
            "M7_min_expected_domains": MIN_EXPECTED_DOMAINS,
            "section_b_score": round(section_b_score, 2),
            "section_b_weight": 0.20,
        }

    def _compute_rouge(self, hypothesis: str, reference: str) -> dict[str, float]:
        """Compute ROUGE-1, ROUGE-2, ROUGE-L scores."""
        from rouge_score import rouge_scorer

        scorer = rouge_scorer.RougeScorer(
            ["rouge1", "rouge2", "rougeLsum"], use_stemmer=True
        )
        scores = scorer.score(reference, hypothesis)
        return {
            "rouge1": scores["rouge1"].fmeasure,
            "rouge2": scores["rouge2"].fmeasure,
            "rougeLsum": scores["rougeLsum"].fmeasure,
        }

    def _compute_bertscore(self, hypothesis: str, reference: str) -> float:
        """
        Compute BERTScore F1 using the configured lightweight model.
        Truncates text to avoid memory issues with large reports.
        """
        from bert_score import score as bert_score_fn

        # Truncate to avoid excessive memory / time consumption
        max_chars = 4000
        hyp_trunc = hypothesis[:max_chars]
        ref_trunc = reference[:max_chars]

        P, R, F1 = bert_score_fn(
            [hyp_trunc],
            [ref_trunc],
            model_type=settings.BERTSCORE_MODEL,
            lang="en",
            verbose=False,
            rescale_with_baseline=False,
        )
        return float(F1[0])

    def _compute_groundedness(
        self,
        final_report: str,
        raw_search_cache: list[dict] | None = None,
    ) -> float:
        """
        Groundedness measures how well the cited sources in Section 13 correspond to
        sources that were actually scraped during the research job.

        When raw_search_cache is available (preferred):
            score = (cited Section 13 URLs that appear in the cache) / MIN_EXPECTED_DOMAINS
            This verifies that cited sources were genuinely scraped, not hallucinated.

        Fallback (no cache):
            score = unique domains in Section 13 / MIN_EXPECTED_DOMAINS
            Original behaviour — counts diversity of cited sources.

        Capped at 1.0.
        """
        section_13 = self._extract_section_13(final_report)
        if not section_13:
            logger.warning(
                "[Evaluation] Section 13 (Source Summary) not found in report"
            )
            return 0.0

        cited_urls = self._extract_urls(section_13)

        if raw_search_cache:
            # Build the set of URLs (and domains) that were actually scraped
            cached_urls = {
                e["url"].strip().lower()
                for e in raw_search_cache
                if e.get("url", "").strip()
            }
            cached_domains = set()
            for url in cached_urls:
                try:
                    from urllib.parse import urlparse as _up

                    netloc = _up(url).netloc.lower().removeprefix("www.")
                    if netloc:
                        cached_domains.add(netloc)
                except Exception:
                    pass

            # Count cited domains that match any scraped domain
            verified = 0
            for url in cited_urls:
                try:
                    from urllib.parse import urlparse as _up

                    netloc = _up(url).netloc.lower().removeprefix("www.")
                    if netloc and netloc in cached_domains:
                        verified += 1
                except Exception:
                    pass

            score = min(1.0, verified / MIN_EXPECTED_DOMAINS)
            logger.debug(
                f"[Evaluation] Groundedness (cache): {verified} cited URLs verified "
                f"against {len(cached_domains)} scraped domains → {score:.3f}"
            )
        else:
            unique_domains = self._count_unique_domains(cited_urls)
            score = min(1.0, unique_domains / MIN_EXPECTED_DOMAINS)
            logger.debug(
                f"[Evaluation] Groundedness (fallback): {unique_domains} unique domains "
                f"in Section 13 → {score:.3f}"
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

    def _compute_source_diversity(
        self,
        final_report: str,
        raw_search_cache: list[dict] | None = None,
    ) -> float:
        """
        Source Diversity measures the breadth of research sources used.

        When raw_search_cache is available (preferred):
            score = unique domains across ALL scraped entries / MIN_EXPECTED_DOMAINS
            This reflects true research breadth regardless of what was cited in the report.

        Fallback (no cache):
            score = unique domains cited anywhere in the report / MIN_EXPECTED_DOMAINS
            Original behaviour.

        Capped at 1.0.
        """
        if raw_search_cache:
            all_cache_urls = [
                e["url"] for e in raw_search_cache if e.get("url", "").strip()
            ]
            unique_domains = self._count_unique_domains(all_cache_urls)
            source = "cache"
        else:
            all_urls = self._extract_urls(final_report)
            unique_domains = self._count_unique_domains(all_urls)
            source = "report"

        score = min(1.0, unique_domains / MIN_EXPECTED_DOMAINS)
        logger.debug(
            f"[Evaluation] Source Diversity ({source}): {unique_domains} unique domains → {score:.3f}"
        )
        return score

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

    def _session_state_to_text(self, session_state: dict[str, Any]) -> str:
        """
        Convert session state dict to a single flat text blob for use as
        the ROUGE/BERTScore reference.  We concatenate all agent output values.
        """
        parts = []
        for _key, value in session_state.items():
            if isinstance(value, dict):
                parts.append(json.dumps(value, ensure_ascii=False))
            elif isinstance(value, str) and value:
                parts.append(value)
        return "\n\n".join(parts)

    def _cache_to_text(self, raw_search_cache: list[dict]) -> str:
        """
        Convert the raw search cache into a flat text blob for ROUGE/BERTScore
        reference.  Concatenates titles and snippets from all cache entries.
        """
        parts: list[str] = []
        for entry in raw_search_cache:
            title = entry.get("title", "").strip()
            snippet = entry.get("snippet", "").strip()
            if title:
                parts.append(title)
            if snippet:
                parts.append(snippet)
        return "\n\n".join(parts)

    @staticmethod
    def _cache_to_evidence_block(
        raw_search_cache: list[dict],
        max_chars: int = 8000,
    ) -> str:
        """
        Build a condensed evidence block from the raw search cache for inclusion
        in the LLM judge prompt.  Each entry is formatted as:

            [Agent: <agent>] <title>
            URL: <url>
            <snippet>

        Entries are included in insertion order until max_chars is reached.
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

    def _load_catalog_text(self) -> str:
        """
        Load and cache Colt Product Catalog PDF text.
        Returns empty string if file not found (non-fatal).
        """
        if self._catalog_loaded:
            return self._catalog_text or ""

        self._catalog_loaded = True
        catalog_path = Path(settings.COLT_PRODUCT_CATALOG_PATH)

        # Resolve relative path from project root (2 levels up from this file)
        if not catalog_path.is_absolute():
            project_root = Path(__file__).resolve().parents[2]
            catalog_path = project_root / catalog_path

        if not catalog_path.exists():
            logger.warning(
                f"[Evaluation] ColtProductCatalog not found at {catalog_path}. "
                "LLM judge will operate without catalog context."
            )
            self._catalog_text = ""
            return ""

        try:
            from pypdf import PdfReader

            reader = PdfReader(str(catalog_path))
            pages_text = []
            for page in reader.pages:
                text = page.extract_text()
                if text:
                    pages_text.append(text)
            self._catalog_text = "\n\n".join(pages_text)
            logger.info(
                f"[Evaluation] Loaded ColtProductCatalog: "
                f"{len(reader.pages)} pages, {len(self._catalog_text)} chars"
            )
        except Exception as e:
            logger.warning(f"[Evaluation] Failed to load ColtProductCatalog: {e}")
            self._catalog_text = ""

        return self._catalog_text or ""
