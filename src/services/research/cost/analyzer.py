"""Cost analysis for LLM tokens and search API calls."""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict

from src.core.config import settings
from src.core.logging_config import logger


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
    model_version: str  # "3.x" or "2.x"
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


class CostAnalyzer:
    """Analyze costs for research jobs."""

    def __init__(self):
        self.pricing = self._parse_pricing()
        self.search_pricing_3x = float(settings.GOOGLE_SEARCH_PRICING_3X or 14.0)
        self.search_pricing_2x = float(settings.GOOGLE_SEARCH_PRICING_2X or 35.0)

    def _parse_pricing(self) -> dict[str, dict[str, float]]:
        """Parse model pricing from config."""
        try:
            pricing_json = settings.GEMINI_MODEL_PRICING_JSON
            if isinstance(pricing_json, str):
                return json.loads(pricing_json)
            return pricing_json or {}
        except Exception as e:
            logger.warning(f"Failed to parse pricing config: {e}")
            return {
                "gemini-2.5-pro": {"input_per_1m": 1.25, "output_per_1m": 10.0},
                "gemini-2.5-flash": {"input_per_1m": 0.30, "output_per_1m": 2.50},
            }

    def _extract_model_version(self, model_name: str) -> str:
        """Extract major version from model name."""
        if "3.5" in model_name or "3.0" in model_name:
            return "3.x"
        elif "2.5" in model_name or "2.0" in model_name:
            return "2.x"
        return "2.x"

    def calculate_token_cost(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> TokenCost:
        """Calculate token cost for a model."""
        pricing = self.pricing.get(model, {})

        # Fallback pricing
        if not pricing:
            if "3.5" in model or "3.0" in model:
                pricing = {"input_per_1m": 1.5, "output_per_1m": 9.0}
            else:
                pricing = {"input_per_1m": 0.30, "output_per_1m": 2.50}

        input_cost = (input_tokens / 1_000_000) * pricing.get("input_per_1m", 0)
        output_cost = (output_tokens / 1_000_000) * pricing.get("output_per_1m", 0)

        return TokenCost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_usd=round(input_cost, 6),
            output_cost_usd=round(output_cost, 6),
        )

    def calculate_search_cost(
        self, search_count: int, model: str | None = None
    ) -> SearchCost:
        """Calculate search API cost."""
        if model is None:
            model = settings.SEARCH_AGENT_MODEL

        model_version = self._extract_model_version(model)
        cost_per_1k = (
            self.search_pricing_3x if model_version == "3.x" else self.search_pricing_2x
        )

        total_cost = (search_count / 1000) * cost_per_1k
        if total_cost < 0.01 and search_count > 0:
            total_cost = 0.01  # Minimum billing

        return SearchCost(
            search_count=search_count,
            model_version=model_version,
            cost_per_1k=cost_per_1k,
            total_cost_usd=round(total_cost, 6),
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

        total_cost = token_cost.total_cost + search_cost.total_cost_usd

        analysis = CostAnalysis(
            token_cost=token_cost,
            search_cost=search_cost,
            total_cost_usd=round(total_cost, 6),
        )

        logger.info(
            f"Cost analysis: tokens=${token_cost.total_cost:.6f}, "
            f"searches=${search_cost.total_cost_usd:.6f}, "
            f"total=${total_cost:.6f}"
        )
        return analysis
