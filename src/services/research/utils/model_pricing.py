"""Per-model Gemini token pricing and session usage tracking."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from ....core.config import settings
from ....core.logging_config import logger

TOKENS_BY_MODEL_KEY = "mc_tokens_by_model"
INVOCATION_MODELS_KEY = "mc_invocation_models"
SEARCH_AGENT_NAME = "google_search_agent"


@dataclass(frozen=True)
class ModelPricing:
    input_per_1m: float
    output_per_1m: float


def normalize_model_name(model: str) -> str:
    """Normalize model id for registry lookup."""
    name = str(model).strip()
    if name.startswith("models/"):
        name = name[len("models/") :]
    return name.lower()


@lru_cache(maxsize=1)
def load_pricing_registry() -> dict[str, ModelPricing]:
    """Parse GEMINI_MODEL_PRICING_JSON into a model -> pricing map."""
    raw = settings.GEMINI_MODEL_PRICING_JSON
    data = json.loads(raw) if isinstance(raw, str) else raw

    registry: dict[str, ModelPricing] = {}
    for model, rates in data.items():
        registry[normalize_model_name(model)] = ModelPricing(
            input_per_1m=float(rates["input_per_1m"]),
            output_per_1m=float(rates["output_per_1m"]),
        )
    return registry


def get_model_pricing(model: str) -> ModelPricing | None:
    """Return pricing for a model, or None when not configured."""
    return load_pricing_registry().get(normalize_model_name(model))


def calculate_token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for token usage on a specific model."""
    pricing = get_model_pricing(model)
    if pricing is None:
        logger.warning(
            "[ModelPricing] No pricing configured for model=%s — cost set to $0",
            model,
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


def resolve_agent_model(agent_name: str) -> str:
    """Fallback model assignment when request metadata is unavailable."""
    if agent_name == SEARCH_AGENT_NAME:
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
    # ADK session state may not support __delitem__; assign empty dict instead.
    state[INVOCATION_MODELS_KEY] = inv_models
    return model
