from __future__ import annotations

import pytest

from src.services.research.utils import model_pricing
from src.services.research.utils.metrics import calculate_metrics, reconcile_cost


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
        "mc_source_domains": ["example.com"],
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
