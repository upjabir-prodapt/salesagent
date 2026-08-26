"""Pydantic Model Registry for model selection, rates, regions, and context windows."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def normalize_model_id(model: str) -> str:
    """Normalize model identifier (strips 'models/' prefix and lowercases)."""
    norm = str(model).strip()
    if norm.startswith("models/"):
        norm = norm[len("models/") :]
    return norm.lower()


class PricingTier(BaseModel):
    """Pricing tier for a model."""

    max_input_tokens: int | None = None
    input_cost_per_1k: float
    output_cost_per_1k: float
    cache_hit_cost_per_1k: float = 0.0


class ModelInfo(BaseModel):
    """Encapsulates metadata, region, context length, and pricing for an LLM model."""

    model_id: str
    provider: str = "gemini_vertexai"
    region: str | None = None
    context_window_tokens: int = 1_048_576
    search_cost_per_1k: float = 35.0
    tiers: list[PricingTier] = Field(default_factory=list)

    @property
    def input_per_1m(self) -> float:
        if self.tiers:
            return round(self.tiers[0].input_cost_per_1k * 1000, 6)
        return 1.50

    @property
    def output_per_1m(self) -> float:
        if self.tiers:
            return round(self.tiers[0].output_cost_per_1k * 1000, 6)
        return 9.00

    @property
    def cache_hit_per_1m(self) -> float:
        if self.tiers:
            return round(self.tiers[0].cache_hit_cost_per_1k * 1000, 6)
        return 0.15

    def calculate_token_cost(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            (input_tokens / 1_000_000) * self.input_per_1m
            + (output_tokens / 1_000_000) * self.output_per_1m,
            6,
        )

    def calculate_search_cost(self, search_count: int) -> float:
        cost = (search_count / 1000) * self.search_cost_per_1k
        if cost < 0.01 and search_count > 0:
            return 0.01
        return round(cost, 6)


class ModelRegistry(BaseModel):
    """Pydantic model registry mapping model identifiers to ModelInfo objects."""

    models: dict[str, ModelInfo] = Field(default_factory=dict)

    def get_model(self, model_id: str, region: str | None = None) -> ModelInfo:
        norm = normalize_model_id(model_id)
        if region:
            reg_key = f"{norm}:{region.strip().lower()}"
            if reg_key in self.models:
                return self.models[reg_key]
        if norm in self.models:
            return self.models[norm]
        return ModelInfo(
            model_id=norm,
            region=region,
            tiers=[
                PricingTier(
                    input_cost_per_1k=0.00165 if region == "europe-west3" else 0.0015,
                    output_cost_per_1k=0.0099 if region == "europe-west3" else 0.009,
                )
            ],
        )

    @classmethod
    def from_catalog_dict(
        cls, data: list[dict[str, Any]] | dict[str, Any]
    ) -> ModelRegistry:
        models: dict[str, ModelInfo] = {}
        items = data if isinstance(data, list) else [data]
        for item in items:
            raw_id = item.get("model_id") or ""
            model_id = normalize_model_id(str(raw_id))
            if not model_id:
                continue
            region = item.get("region")
            reg_str = str(region).strip().lower() if region else None
            info = ModelInfo(
                model_id=model_id,
                provider=str(item.get("provider") or "gemini_vertexai"),
                region=reg_str,
                context_window_tokens=int(
                    item.get("context_window_tokens") or 1_048_576
                ),
                search_cost_per_1k=float(item.get("search_cost_per_1k") or 35.0),
                tiers=[
                    PricingTier(
                        max_input_tokens=t.get("max_input_tokens"),
                        input_cost_per_1k=float(t["input_cost_per_1k"]),
                        output_cost_per_1k=float(t["output_cost_per_1k"]),
                        cache_hit_cost_per_1k=float(
                            t.get("cache_hit_cost_per_1k", 0.0)
                        ),
                    )
                    for t in (item.get("tiers") or [])
                    if "input_cost_per_1k" in t and "output_cost_per_1k" in t
                ],
            )
            if reg_str:
                models[f"{model_id}:{reg_str}"] = info
            if model_id not in models or reg_str is None:
                models[model_id] = info
        return cls(models=models)

    @classmethod
    def from_catalog_file(cls, catalog_path: Path) -> ModelRegistry:
        if not catalog_path.is_file():
            raise FileNotFoundError(
                f"Pricing catalog file not found at: {catalog_path}"
            )
        with open(catalog_path, encoding="utf-8") as f:
            return cls.from_catalog_dict(json.load(f))


@lru_cache(maxsize=1)
def get_model_registry(catalog_path_str: str | None = None) -> ModelRegistry:
    """Singleton cached provider for the active ModelRegistry."""
    from src.shared.config import settings

    path = Path(catalog_path_str) if catalog_path_str else settings.pricing_catalog_path
    if path.is_file():
        try:
            return ModelRegistry.from_catalog_file(path)
        except Exception as e:
            logger.error(f"[ModelRegistry] Failed to load from {path}: {e}")
            if not settings.IS_LOCAL:
                raise
    elif not settings.IS_LOCAL:
        raise FileNotFoundError(
            f"pricing_catalog.json not found at {path}. This file is the single source of truth "
            "for model pricing and must be mounted into the asset cache (/secrets/assets/pricing_catalog.json)."
        )

    return ModelRegistry.from_catalog_dict(
        [
            {
                "model_id": "gemini-3.5-flash",
                "region": "europe-west3",
                "context_window_tokens": 1048576,
                "search_cost_per_1k": 35.0,
                "tiers": [
                    {
                        "input_cost_per_1k": 0.00165,
                        "output_cost_per_1k": 0.0099,
                        "cache_hit_cost_per_1k": 0.000165,
                    }
                ],
            },
            {
                "model_id": "gemini-3.5-flash",
                "region": None,
                "context_window_tokens": 1048576,
                "search_cost_per_1k": 35.0,
                "tiers": [
                    {
                        "input_cost_per_1k": 0.0015,
                        "output_cost_per_1k": 0.009,
                        "cache_hit_cost_per_1k": 0.00015,
                    }
                ],
            },
        ]
    )
