"""Tests for research metrics helpers."""

from src.services.research.metrics import calculate_metrics, reconcile_cost


def test_calculate_metrics_with_cost():
    state = {"mc_input_tokens": 1000, "mc_output_tokens": 500, "mc_temperature": 0.2}
    metrics = calculate_metrics(state, latency=12.5)
    assert metrics["total_tokens"] == 1500
    assert metrics["latency"] == 12.5
    assert metrics["cost_usd"] is not None


def test_reconcile_cost_ok():
    state = {
        "agent_telemetry_records": [
            {"tokens_input": 100, "tokens_output": 50, "cost_usd": 0.01}
        ]
    }
    metrics = {"input_tokens": 100, "output_tokens": 50, "cost_usd": 0.01}
    result = reconcile_cost(state, metrics)
    assert result["delta_cost_usd"] == 0.0
    assert result["agent_record_count"] == 1
