"""Helpers for research status metadata shaping."""

from __future__ import annotations

from collections import Counter
from typing import Any

from ...core.config import settings


def build_completion_metadata(
    *,
    latency: float,
    metrics: dict[str, Any],
    pdf_available: bool,
    side_op_failures: dict[str, str],
    reconciliation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "model_version": settings.GEMINI_MODEL,
        "latency_seconds": latency,
        "tokens_used": metrics["total_tokens"] or None,
        "cost_usd": metrics["cost_usd"],
        "pdf_available": pdf_available,
        "side_op_failures": side_op_failures or None,
        "current_agent": None,
    }
    if reconciliation:
        meta["cost_reconciliation"] = reconciliation
    return meta


def build_failure_summary(violations: list[Any]) -> dict[str, Any]:
    """Construct a structured summary of guardrail violations."""
    rule_counts = Counter(v.rule for v in violations)
    dominant_rule = rule_counts.most_common(1)[0][0] if rule_counts else "unknown"
    return {
        "dominant_rule": dominant_rule,
        "all_violations": [{"rule": v.rule, "detail": v.detail} for v in violations],
    }


def build_model_card(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_version": metadata.get("model_version"),
        "tokens_used": metadata.get("tokens_used"),
        "latency_seconds": metadata.get("latency_seconds"),
        "cost_usd": metadata.get("cost_usd"),
    }

