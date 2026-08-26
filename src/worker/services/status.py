"""Status metadata and model card builders."""

from __future__ import annotations

from typing import Any

from src.shared.config import settings


def build_completion_metadata(
    latency: float,
    metrics: dict[str, Any],
    pdf_available: bool = False,
    side_op_failures: dict[str, Any] | None = None,
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build standardized completion metadata dict for BigQuery status updates."""
    meta: dict[str, Any] = {
        "model_version": settings.GEMINI_MODEL,
        "latency_seconds": latency,
        "tokens_used": metrics.get("total_tokens", 0),
        "cost_usd": metrics.get("cost_usd"),
        "temperature": metrics.get("temperature"),
        "pdf_available": pdf_available,
        "side_op_failures": side_op_failures or {},
    }
    if reconciliation:
        meta["cost_reconciliation"] = reconciliation
    return meta


def build_failure_summary(violations: list[Any]) -> dict[str, Any]:
    """Build normalized failure summary dict for BigQuery status updates."""
    if not violations:
        return {
            "dominant_rule": "unknown",
            "all_violations": [],
        }

    rule_counts: dict[str, int] = {}
    formatted_violations = []

    for v in violations:
        rule = getattr(v, "rule", None) or (
            v.get("rule") if isinstance(v, dict) else "unknown"
        )
        detail = getattr(v, "detail", None) or (
            v.get("detail") if isinstance(v, dict) else str(v)
        )
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
        formatted_violations.append({"rule": rule, "detail": detail})

    dominant_rule = max(rule_counts.items(), key=lambda x: x[1])[0]

    return {
        "dominant_rule": dominant_rule,
        "all_violations": formatted_violations,
    }


def build_model_card(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build model card describing the sales agent pipeline execution."""
    if metadata:
        return {
            "model_version": metadata.get("model_version", settings.GEMINI_MODEL),
            "tokens_used": metadata.get("tokens_used", 0),
            "latency_seconds": metadata.get("latency_seconds", 0.0),
            "cost_usd": metadata.get("cost_usd"),
            "temperature": metadata.get("temperature"),
        }

    return {
        "pipeline_version": "2.0.0",
        "primary_model": settings.GEMINI_MODEL,
        "architecture": "SalesResearchWorkflowAgent",
        "domains": 12,
        "search_budget": 30,
        "cache_ttl_days": 7,
    }


__all__ = [
    "build_completion_metadata",
    "build_failure_summary",
    "build_model_card",
]
