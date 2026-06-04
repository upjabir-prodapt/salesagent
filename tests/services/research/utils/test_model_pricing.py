from __future__ import annotations

import pytest

from src.services.research.utils import model_pricing


@pytest.fixture(autouse=True)
def _clear_pricing_cache() -> None:
    model_pricing.load_pricing_registry.cache_clear()
    yield
    model_pricing.load_pricing_registry.cache_clear()


def test_normalize_model_name_strips_prefix() -> None:
    assert (
        model_pricing.normalize_model_name("models/gemini-2.5-pro") == "gemini-2.5-pro"
    )


def test_calculate_token_cost_flash() -> None:
    cost = model_pricing.calculate_token_cost("gemini-2.5-flash", 1_000_000, 1_000_000)
    assert cost == pytest.approx(2.80)


def test_calculate_token_cost_pro() -> None:
    cost = model_pricing.calculate_token_cost("gemini-2.5-pro", 1_000_000, 1_000_000)
    assert cost == pytest.approx(11.25)


def test_calculate_token_cost_unknown_model_returns_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    cost = model_pricing.calculate_token_cost("unknown-model", 1000, 1000)
    assert cost == 0.0
    assert "No pricing configured" in caplog.text


def test_record_token_usage_updates_session_state() -> None:
    state: dict = {}
    model_pricing.record_token_usage(state, "gemini-2.5-pro", 100, 50)
    model_pricing.record_token_usage(state, "gemini-2.5-flash", 200, 25)

    assert state["mc_input_tokens"] == 300
    assert state["mc_output_tokens"] == 75
    assert state["mc_tokens_by_model"]["gemini-2.5-pro"] == {
        "input": 100,
        "output": 50,
    }
    assert state["mc_tokens_by_model"]["gemini-2.5-flash"] == {
        "input": 200,
        "output": 25,
    }


def test_calculate_total_cost_mixed_models() -> None:
    tokens_by_model = {
        "gemini-2.5-pro": {"input": 100_000, "output": 10_000},
        "gemini-2.5-flash": {"input": 50_000, "output": 5_000},
    }
    expected = model_pricing.calculate_token_cost(
        "gemini-2.5-pro", 100_000, 10_000
    ) + model_pricing.calculate_token_cost("gemini-2.5-flash", 50_000, 5_000)
    assert model_pricing.calculate_total_cost(tokens_by_model) == pytest.approx(
        round(expected, 6)
    )


def test_calculate_delta_cost() -> None:
    snap = {"gemini-2.5-pro": {"input": 100, "output": 10}}
    current = {
        "gemini-2.5-pro": {"input": 1100, "output": 110},
        "gemini-2.5-flash": {"input": 500, "output": 50},
    }
    expected = model_pricing.calculate_token_cost(
        "gemini-2.5-pro", 1000, 100
    ) + model_pricing.calculate_token_cost("gemini-2.5-flash", 500, 50)
    assert model_pricing.calculate_delta_cost(snap, current) == pytest.approx(
        round(expected, 6)
    )


def test_resolve_model_used_for_delta() -> None:
    snap = {"gemini-2.5-pro": {"input": 0, "output": 0}}
    current = {"gemini-2.5-flash": {"input": 100, "output": 10}}
    assert (
        model_pricing.resolve_model_used_for_delta(snap, current, "StrategyAgent")
        == "gemini-2.5-flash"
    )

    mixed_current = {
        "gemini-2.5-pro": {"input": 100, "output": 10},
        "gemini-2.5-flash": {"input": 50, "output": 5},
    }
    assert (
        model_pricing.resolve_model_used_for_delta({}, mixed_current, "StrategyAgent")
        == "mixed"
    )
