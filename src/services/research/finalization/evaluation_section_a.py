"""Section A scoring helpers (LLM-as-judge parsing and penalties)."""

from __future__ import annotations

from typing import Any

from .evaluation_config import DIMENSION_CONFIG, MAX_SECTION_A_WEIGHTED_SCORE


def parse_and_score_section_a(llm_output: dict[str, Any]) -> dict[str, Any]:
    dimension_results = {}
    total_weighted = 0.0

    for dim_key, cfg in DIMENSION_CONFIG.items():
        raw_score = llm_output.get(dim_key, 0)
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

    m12_count = max(0, int(llm_output.get("M12_hallucination_count", 0)))
    m13_count = max(0, int(llm_output.get("M13_policy_violation_count", 0)))
    penalty_deduction = (m12_count * 10) + (m13_count * 15)

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


def empty_section_a(error: str = "") -> dict[str, Any]:
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
