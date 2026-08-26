"""Unit tests for Pydantic ModelRegistry and ModelInfo."""

from __future__ import annotations

import pytest

from src.shared.config import settings
from src.shared.model_registry import (
    ModelInfo,
    PricingTier,
    get_model_registry,
    normalize_model_id,
)


def test_normalize_model_id():
    assert normalize_model_id("models/gemini-3.5-flash") == "gemini-3.5-flash"
    assert normalize_model_id("GEMINI-2.5-PRO") == "gemini-2.5-pro"


def test_model_info_calculations():
    tier = PricingTier(
        input_cost_per_1k=0.00165,
        output_cost_per_1k=0.0099,
        cache_hit_cost_per_1k=0.000165,
    )
    info = ModelInfo(
        model_id="gemini-3.5-flash",
        region="europe-west3",
        context_window_tokens=1048576,
        search_cost_per_1k=35.0,
        tiers=[tier],
    )
    assert info.input_per_1m == pytest.approx(1.65)
    assert info.output_per_1m == pytest.approx(9.90)
    assert info.cache_hit_per_1m == pytest.approx(0.165)
    assert info.calculate_token_cost(100_000, 10_000) == pytest.approx(0.264)
    assert info.calculate_search_cost(30) == pytest.approx(1.05)


def test_model_registry_lookup_from_catalog():
    registry = get_model_registry()

    # Global lookup
    flash_global = registry.get_model("gemini-3.5-flash")
    assert flash_global is not None
    assert flash_global.input_per_1m == pytest.approx(1.50)
    assert flash_global.output_per_1m == pytest.approx(9.00)

    # Regional lookup
    flash_regional = registry.get_model("gemini-3.5-flash", region="europe-west3")
    assert flash_regional is not None
    assert flash_regional.region == "europe-west3"
    assert flash_regional.input_per_1m == pytest.approx(1.65)
    assert flash_regional.output_per_1m == pytest.approx(9.90)


def test_settings_model_properties():
    assert settings.LLM_MODEL == "gemini-3.5-flash"
    assert settings.SEARCH_MODEL == "gemini-3.5-flash"
    assert settings.llm_model_info.model_id == "gemini-3.5-flash"
    assert settings.search_model_info.model_id == "gemini-3.5-flash"
