"""Model card metrics and cost reconciliation for research jobs."""

from __future__ import annotations

from ....core.logging_config import logger
from .model_pricing import (
    calculate_total_cost,
    total_tokens_from_by_model,
)


def calculate_metrics(session_state: dict, latency: float) -> dict:
    """Extract and calculate model card metrics from session state."""
    tokens_by_model = session_state.get("mc_tokens_by_model") or {}
    if tokens_by_model:
        input_tokens, output_tokens = total_tokens_from_by_model(tokens_by_model)
        cost_usd = calculate_total_cost(tokens_by_model)
    else:
        input_tokens = session_state.get("mc_input_tokens") or 0
        output_tokens = session_state.get("mc_output_tokens") or 0
        cost_usd = None

    total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "latency": latency,
        "cost_usd": cost_usd,
        "temperature": session_state.get("mc_temperature"),
    }


def reconcile_cost(session_state: dict, metrics: dict) -> dict:
    """Cross-check session-level cost against per-agent telemetry aggregation."""
    telemetry_records = session_state.get("agent_telemetry_records") or []
    per_agent_input = sum(r.get("tokens_input") or 0 for r in telemetry_records)
    per_agent_output = sum(r.get("tokens_output") or 0 for r in telemetry_records)
    per_agent_cost = sum(r.get("cost_usd") or 0.0 for r in telemetry_records)

    session_input = metrics.get("input_tokens") or 0
    session_output = metrics.get("output_tokens") or 0
    session_cost = metrics.get("cost_usd") or 0.0

    delta_input = abs(session_input - per_agent_input)
    delta_output = abs(session_output - per_agent_output)
    delta_cost = abs(session_cost - per_agent_cost)

    reconciliation = {
        "session_input_tokens": session_input,
        "session_output_tokens": session_output,
        "per_agent_input_tokens": per_agent_input,
        "per_agent_output_tokens": per_agent_output,
        "delta_input_tokens": delta_input,
        "delta_output_tokens": delta_output,
        "per_agent_cost_usd": round(per_agent_cost, 6),
        "session_cost_usd": round(session_cost, 6),
        "delta_cost_usd": round(delta_cost, 6),
        "agent_record_count": len(telemetry_records),
    }

    if delta_cost > 0.10 or delta_input > 5000 or delta_output > 5000:
        logger.warning(
            "[CostReconciliation] Notable discrepancy — "
            "session_tokens=%d per_agent_tokens=%d delta_cost=$%.4f",
            session_input + session_output,
            per_agent_input + per_agent_output,
            delta_cost,
        )
    else:
        logger.info(
            "[CostReconciliation] OK — session_tokens=%d per_agent_tokens=%d "
            "delta_cost=$%.6f agent_records=%d",
            session_input + session_output,
            per_agent_input + per_agent_output,
            delta_cost,
            len(telemetry_records),
        )
    return reconciliation
