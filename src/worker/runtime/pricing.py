"""Per-model Gemini token pricing, search cost calculation, and session usage tracking."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Any

from src.shared.config import settings
from src.shared.logging_config import logger

TOKENS_BY_MODEL_KEY = "mc_tokens_by_model"
INVOCATION_MODELS_KEY = "mc_invocation_models"
SEARCH_TOOL_NAME = "google_search"
SEARCH_AGENT_NAME = "google_search_agent"


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float
    output_per_1m: float
    cache_hit_per_1m: float = 0.0
    context_window_tokens: int | None = None
    search_cost_per_1k: float = 35.0
    region: str | None = None


@dataclass
class TokenCost:
    """Token cost breakdown."""

    input_tokens: int
    output_tokens: int
    input_cost_usd: float
    output_cost_usd: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def total_cost(self) -> float:
        return self.input_cost_usd + self.output_cost_usd


@dataclass
class SearchCost:
    """Search cost breakdown."""

    search_count: int
    model_version: str
    cost_per_1k: float
    total_cost_usd: float


@dataclass
class CostAnalysis:
    """Complete cost breakdown."""

    token_cost: TokenCost
    search_cost: SearchCost
    total_cost_usd: float

    def to_dict(self) -> dict:
        return {
            "tokens": asdict(self.token_cost),
            "searches": asdict(self.search_cost),
            "total_usd": self.total_cost_usd,
        }


def normalize_model_name(model: str) -> str:
    """Normalize model id for registry lookup."""
    name = str(model).strip()
    if name.startswith("models/"):
        name = name[len("models/") :]
    return name.lower()


@lru_cache(maxsize=1)
def load_pricing_registry() -> dict[str, ModelPricing]:
    """Load pricing from mounted pricing_catalog.json or fallback to GEMINI_MODEL_PRICING_JSON."""
    registry: dict[str, ModelPricing] = {}

    # 1. Primary: load from mounted pricing_catalog.json if present
    catalog_path = getattr(settings, "pricing_catalog_path", None)
    if catalog_path and catalog_path.is_file():
        try:
            with open(catalog_path, encoding="utf-8") as f:
                catalog_data = json.load(f)
            if isinstance(catalog_data, list):
                for item in catalog_data:
                    model_id = normalize_model_name(str(item.get("model_id", "")))
                    if not model_id:
                        continue
                    region = item.get("region")
                    context_window = item.get("context_window_tokens")
                    search_cost_1k = float(item.get("search_cost_per_1k", 35.0))
                    tiers = item.get("tiers") or []
                    tier0 = tiers[0] if tiers else {}
                    input_per_1k = float(tier0.get("input_cost_per_1k", 0.0))
                    output_per_1k = float(tier0.get("output_cost_per_1k", 0.0))
                    cache_hit_per_1k = float(tier0.get("cache_hit_cost_per_1k", 0.0))

                    pricing = ModelPricing(
                        input_per_1m=input_per_1k * 1000,
                        output_per_1m=output_per_1k * 1000,
                        cache_hit_per_1m=cache_hit_per_1k * 1000,
                        context_window_tokens=context_window,
                        search_cost_per_1k=search_cost_1k,
                        region=str(region) if region else None,
                    )
                    # Store region-specific key e.g. "gemini-3.5-flash:europe-west3"
                    if region:
                        registry[f"{model_id}:{str(region).strip().lower()}"] = pricing
                    # Store general key if not already set or region is null
                    if model_id not in registry or region is None:
                        registry[model_id] = pricing

                if registry:
                    return registry
        except Exception as e:
            logger.warning(
                f"[ModelPricing] Failed to parse pricing catalog from {catalog_path}: {e}"
            )

    # 2. Fallback: Default Model Registry
    from src.shared.model_registry import get_model_registry

    default_reg = get_model_registry()
    for k, info in default_reg.models.items():
        registry[k] = ModelPricing(
            input_per_1m=info.input_per_1m,
            output_per_1m=info.output_per_1m,
            cache_hit_per_1m=info.cache_hit_per_1m,
            context_window_tokens=info.context_window_tokens,
            search_cost_per_1k=info.search_cost_per_1k,
            region=info.region,
        )
    return registry


def get_model_pricing(model: str, region: str | None = None) -> ModelPricing | None:
    """Return pricing for a model (with optional region), or None when not configured."""
    normalized = normalize_model_name(model)
    registry = load_pricing_registry()
    if region:
        reg_key = f"{normalized}:{region.strip().lower()}"
        if reg_key in registry:
            return registry[reg_key]
    return registry.get(normalized)


def calculate_token_cost(
    model: str, input_tokens: int, output_tokens: int, region: str | None = None
) -> float:
    """Estimate USD cost for token usage on a specific model."""
    pricing = get_model_pricing(model, region=region)
    if pricing is None:
        logger.warning(
            "[ModelPricing] No pricing configured for model=%s region=%s — cost set to $0",
            model,
            region,
        )
        return 0.0
    return (input_tokens / 1_000_000) * pricing.input_per_1m + (
        output_tokens / 1_000_000
    ) * pricing.output_per_1m


def snapshot_tokens_by_model(state: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Deep-copy per-model token counters for delta snapshots."""
    return copy.deepcopy(state.get(TOKENS_BY_MODEL_KEY) or {})


def total_tokens_from_by_model(
    tokens_by_model: dict[str, dict[str, int]],
) -> tuple[int, int]:
    """Sum input and output tokens across all models."""
    input_total = sum(int(v.get("input") or 0) for v in tokens_by_model.values())
    output_total = sum(int(v.get("output") or 0) for v in tokens_by_model.values())
    return input_total, output_total


def calculate_total_cost(tokens_by_model: dict[str, dict[str, int]]) -> float:
    """Sum estimated cost across all models in a token map."""
    if not tokens_by_model:
        return 0.0
    total = 0.0
    for model, counts in tokens_by_model.items():
        total += calculate_token_cost(
            model,
            int(counts.get("input") or 0),
            int(counts.get("output") or 0),
        )
    return round(total, 6)


def calculate_delta_cost(
    snap: dict[str, dict[str, int]],
    current: dict[str, dict[str, int]],
) -> float:
    """Estimate cost delta between two per-model token snapshots."""
    all_models = set(snap) | set(current)
    total = 0.0
    for model in all_models:
        s = snap.get(model, {})
        c = current.get(model, {})
        delta_in = max(0, int(c.get("input") or 0) - int(s.get("input") or 0))
        delta_out = max(0, int(c.get("output") or 0) - int(s.get("output") or 0))
        total += calculate_token_cost(model, delta_in, delta_out)
    return round(total, 6)


def extract_usage_counts(usage_metadata: Any) -> tuple[int, int]:
    """Extract input/output token counts from GenAI or ADK usage metadata."""
    if usage_metadata is None:
        return 0, 0
    input_t = (
        getattr(usage_metadata, "prompt_token_count", None)
        or getattr(usage_metadata, "input_token_count", None)
        or 0
    )
    output_t = (
        getattr(usage_metadata, "candidates_token_count", None)
        or getattr(usage_metadata, "output_token_count", None)
        or 0
    )
    return int(input_t or 0), int(output_t or 0)


def record_token_usage(
    state: dict[str, Any],
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Accumulate token usage per model and update session totals."""
    if input_tokens <= 0 and output_tokens <= 0:
        return

    normalized = normalize_model_name(model)
    tokens_by_model: dict[str, dict[str, int]] = dict(
        state.get(TOKENS_BY_MODEL_KEY) or {}
    )
    entry = dict(tokens_by_model.get(normalized, {"input": 0, "output": 0}))
    entry["input"] = int(entry.get("input") or 0) + input_tokens
    entry["output"] = int(entry.get("output") or 0) + output_tokens
    tokens_by_model[normalized] = entry
    state[TOKENS_BY_MODEL_KEY] = tokens_by_model

    state["mc_input_tokens"] = (state.get("mc_input_tokens") or 0) + input_tokens
    state["mc_output_tokens"] = (state.get("mc_output_tokens") or 0) + output_tokens


def record_genai_response_usage(
    state: dict[str, Any] | None,
    model: str,
    response: Any,
) -> None:
    """Record token usage from a google.genai generate_content response."""
    if state is None:
        return
    input_t, output_t = extract_usage_counts(getattr(response, "usage_metadata", None))
    record_token_usage(state, model, input_t, output_t)


class CostAnalyzer:
    """Analyze costs for research jobs (tokens + search counts)."""

    def __init__(self):
        self.pricing = load_pricing_registry()

    def calculate_search_cost(
        self, search_count: int, model: str | None = None
    ) -> SearchCost:
        """Calculate search API cost from catalog or defaults."""
        model_name = model or settings.SEARCH_AGENT_MODEL
        pricing = get_model_pricing(model_name)
        cost_per_1k = (
            pricing.search_cost_per_1k
            if pricing and pricing.search_cost_per_1k
            else 35.0
        )
        model_version = "3.x" if ("3.5" in model_name or "3.0" in model_name) else "2.x"
        total_cost = (search_count / 1000) * cost_per_1k
        if total_cost < 0.01 and search_count > 0:
            total_cost = 0.01

        return SearchCost(
            search_count=search_count,
            model_version=model_version,
            cost_per_1k=cost_per_1k,
            total_cost_usd=round(total_cost, 6),
        )

    def calculate_token_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        region: str | None = None,
    ) -> TokenCost:
        """Calculate token cost for a model."""
        pricing = get_model_pricing(model, region=region)
        input_rate = float(pricing.input_per_1m if pricing is not None else 0.0)
        output_rate = float(pricing.output_per_1m if pricing is not None else 0.0)

        if not input_rate and not output_rate:
            if "3.5" in model or "3.0" in model:
                input_rate, output_rate = 1.5, 9.0
            else:
                input_rate, output_rate = 0.30, 2.50

        input_cost = (input_tokens / 1_000_000) * input_rate
        output_cost = (output_tokens / 1_000_000) * output_rate

        return TokenCost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=round(input_cost, 6),
            output_cost_usd=round(output_cost, 6),
        )

    def analyze(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        search_count: int = 0,
    ) -> CostAnalysis:
        """Generate complete cost analysis."""
        token_cost = self.calculate_token_cost(model, input_tokens, output_tokens)
        search_cost = self.calculate_search_cost(search_count, model)
        total_cost = round(token_cost.total_cost + search_cost.total_cost_usd, 6)

        return CostAnalysis(
            token_cost=token_cost,
            search_cost=search_cost,
            total_cost_usd=total_cost,
        )


def resolve_agent_model(agent_name: str) -> str:
    """Fallback model assignment when request metadata is unavailable."""
    if agent_name in (
        SEARCH_TOOL_NAME,
        SEARCH_AGENT_NAME,
        "ParallelSearchAgent",
    ):
        return settings.SEARCH_AGENT_MODEL
    return settings.GEMINI_MODEL


def resolve_model_used_for_delta(
    snap: dict[str, dict[str, int]],
    current: dict[str, dict[str, int]],
    agent_name: str,
) -> str:
    """Pick a representative model label for per-agent telemetry."""
    contributing: list[str] = []
    for model in set(snap) | set(current):
        s = snap.get(model, {})
        c = current.get(model, {})
        if int(c.get("input") or 0) > int(s.get("input") or 0) or int(
            c.get("output") or 0
        ) > int(s.get("output") or 0):
            contributing.append(model)
    if len(contributing) == 1:
        return contributing[0]
    if len(contributing) > 1:
        return "mixed"
    return resolve_agent_model(agent_name)


def store_invocation_model(
    state: dict[str, Any],
    invocation_id: str,
    model: str,
) -> None:
    """Remember which model serves a pending LLM invocation."""
    inv_models: dict[str, str] = dict(state.get(INVOCATION_MODELS_KEY) or {})
    inv_models[invocation_id] = normalize_model_name(model)
    state[INVOCATION_MODELS_KEY] = inv_models


def pop_invocation_model(state: dict[str, Any], invocation_id: str) -> str | None:
    """Resolve and remove a stored invocation model name."""
    inv_models: dict[str, str] = dict(state.get(INVOCATION_MODELS_KEY) or {})
    model = inv_models.pop(invocation_id, None)
    state[INVOCATION_MODELS_KEY] = inv_models
    return model


__all__ = [
    "SEARCH_AGENT_NAME",
    "TOKENS_BY_MODEL_KEY",
    "INVOCATION_MODELS_KEY",
    "ModelPricing",
    "TokenCost",
    "SearchCost",
    "CostAnalysis",
    "CostAnalyzer",
    "normalize_model_name",
    "load_pricing_registry",
    "get_model_pricing",
    "calculate_token_cost",
    "snapshot_tokens_by_model",
    "total_tokens_from_by_model",
    "calculate_total_cost",
    "calculate_delta_cost",
    "resolve_agent_model",
    "resolve_model_used_for_delta",
    "store_invocation_model",
    "pop_invocation_model",
    "extract_usage_counts",
    "record_token_usage",
    "record_genai_response_usage",
]
