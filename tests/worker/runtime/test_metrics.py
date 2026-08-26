from __future__ import annotations

import pytest

from src.worker.runtime import pricing as model_pricing
from src.worker.services.metrics import calculate_metrics, reconcile_cost


@pytest.fixture(autouse=True)
def _clear_pricing_cache() -> None:
    model_pricing.load_pricing_registry.cache_clear()
    yield
    model_pricing.load_pricing_registry.cache_clear()


def test_calculate_metrics_uses_per_model_pricing() -> None:
    session_state = {
        "mc_tokens_by_model": {
            "gemini-2.5-pro": {"input": 100_000, "output": 10_000},
            "gemini-2.5-flash": {"input": 50_000, "output": 5_000},
        },
        "mc_temperature": 0.2,
    }

    metrics = calculate_metrics(session_state, latency=12.5)

    assert metrics["input_tokens"] == 150_000
    assert metrics["output_tokens"] == 15_000
    assert metrics["total_tokens"] == 165_000
    assert metrics["cost_usd"] == pytest.approx(
        model_pricing.calculate_total_cost(session_state["mc_tokens_by_model"])
    )


def test_reconcile_cost_with_model_aware_telemetry() -> None:
    session_state = {
        "mc_tokens_by_model": {
            "gemini-2.5-pro": {"input": 1000, "output": 100},
        },
        "agent_telemetry_records": [
            {
                "tokens_input": 1000,
                "tokens_output": 100,
                "cost_usd": model_pricing.calculate_token_cost(
                    "gemini-2.5-pro", 1000, 100
                ),
            }
        ],
    }
    metrics = calculate_metrics(session_state, latency=1.0)
    reconciliation = reconcile_cost(session_state, metrics)

    assert reconciliation["delta_cost_usd"] == pytest.approx(0.0, abs=1e-6)
    assert reconciliation["delta_input_tokens"] == 0
    assert reconciliation["delta_output_tokens"] == 0


def test_calculate_metrics_legacy_session_fields() -> None:
    session_state = {
        "mc_input_tokens": 100,
        "mc_output_tokens": 50,
        "mc_temperature": 0.1,
    }

    metrics = calculate_metrics(session_state, latency=2.0)

    assert metrics["input_tokens"] == 100
    assert metrics["output_tokens"] == 50
    assert metrics["total_tokens"] == 150
    assert metrics["cost_usd"] is None


def test_reconcile_cost_logs_ok_when_aligned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_state = {
        "mc_input_tokens": 10,
        "mc_output_tokens": 5,
        "agent_telemetry_records": [
            {"tokens_input": 10, "tokens_output": 5, "cost_usd": 0.01},
        ],
    }
    metrics = calculate_metrics(session_state, latency=0.5)

    reconcile_cost(session_state, metrics)

    assert "[CostReconciliation] OK" in caplog.text


def test_reconcile_cost_logs_warning_on_large_delta(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session_state = {
        "mc_input_tokens": 10_000,
        "mc_output_tokens": 10_000,
        "agent_telemetry_records": [
            {"tokens_input": 0, "tokens_output": 0, "cost_usd": 0.0},
        ],
    }
    metrics = {"input_tokens": 10_000, "output_tokens": 10_000, "cost_usd": 5.0}

    reconcile_cost(session_state, metrics)

    assert "[CostReconciliation] Notable discrepancy" in caplog.text
