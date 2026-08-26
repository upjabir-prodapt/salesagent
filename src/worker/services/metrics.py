"""Metrics calculation and cost reconciliation helpers."""

from __future__ import annotations

from typing import Any

from src.shared.logging_config import logger
from src.worker.runtime import pricing as model_pricing


def calculate_metrics(
    session_state: dict[str, Any],
    latency: float,
) -> dict[str, Any]:
    """Calculate token totals, latency, and estimated cost from session state."""
    tokens_by_model = session_state.get("mc_tokens_by_model")

    if tokens_by_model:
        input_tokens = sum(v.get("input", 0) for v in tokens_by_model.values())
        output_tokens = sum(v.get("output", 0) for v in tokens_by_model.values())
        total_tokens = input_tokens + output_tokens
        cost_usd = model_pricing.calculate_total_cost(tokens_by_model)
    else:
        input_tokens = session_state.get("mc_input_tokens") or 0
        output_tokens = session_state.get("mc_output_tokens") or 0
        total_tokens = input_tokens + output_tokens
        cost_usd = None

    temperature = session_state.get("mc_temperature")

    return {
        "temperature": temperature,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "latency": round(latency, 2),
        "cost_usd": cost_usd,
    }


def reconcile_cost(
    session_state: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile metrics with session state telemetry records."""
    records = session_state.get("agent_telemetry_records") or []
    metric_cost = metrics.get("cost_usd") or 0.0
    metric_input = metrics.get("input_tokens") or 0
    metric_output = metrics.get("output_tokens") or 0

    if records:
        telemetry_input = sum(
            r.get("tokens_input") or r.get("input_tokens", 0) for r in records
        )
        telemetry_output = sum(
            r.get("tokens_output") or r.get("output_tokens", 0) for r in records
        )
        telemetry_cost = sum(r.get("cost_usd", 0.0) for r in records)
    else:
        telemetry_input = 0
        telemetry_output = 0
        telemetry_cost = 0.0

    delta_cost_usd = abs(metric_cost - telemetry_cost)
    delta_input = abs(metric_input - telemetry_input)
    delta_output = abs(metric_output - telemetry_output)

    reconciliation = {
        "metric_cost_usd": metric_cost,
        "telemetry_cost_usd": telemetry_cost,
        "delta_cost_usd": delta_cost_usd,
        "delta_input_tokens": delta_input,
        "delta_output_tokens": delta_output,
    }

    if delta_cost_usd > 1.0 or delta_input > 1000 or delta_output > 1000:
        logger.warning(
            f"[CostReconciliation] Notable discrepancy: delta_cost=${delta_cost_usd:.4f}, delta_input={delta_input}, delta_output={delta_output}"
        )
    else:
        logger.info(f"[CostReconciliation] OK: delta_cost=${delta_cost_usd:.4f}")

    return reconciliation


__all__ = ["calculate_metrics", "reconcile_cost"]
