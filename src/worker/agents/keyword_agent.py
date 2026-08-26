"""KeywordGeneratorAgent and BM25 query selection."""

from __future__ import annotations

from datetime import datetime

from google.adk.agents import LlmAgent
from google.adk.models import Gemini

from src.shared.config import settings
from src.worker.domain.schemas import (
    CandidateQueries,
    NormalizedQueryPlan,
    QueryWithMetadata,
)
from src.worker.model import retry_config

from .callbacks import (
    after_agent_callback,
    after_model_callback,
    after_tool_callback,
    before_agent_callback,
    before_model_callback,
    before_tool_callback,
)
from .retrying_agent import RetryingLlmAgent
from .safety import get_safety_config_for_agent


class Bm25QuerySelector:
    """Select top N queries using BM25-like ranking and deduplication."""

    DOMAIN_LIMITS = {
        "firmographics": 3,
        "geographic": 2,
        "executive": 3,
        "strategy": 3,
        "compliance": 2,
        "market": 3,
        "ecosystem": 2,
        "tech_stack": 3,
        "procurement": 2,
        "growth_signals": 2,
        "risk_signals": 3,
        "campaign_signals": 2,
    }

    TOTAL_BUDGET = 30

    def __init__(self, company_name: str):
        self.company_name = company_name

    def _compute_bm25_score(self, query: str, company_name: str) -> float:
        score = 0.0
        if company_name.lower() in query.lower():
            score += 2.0
        for word in query.split():
            if word.isdigit() and len(word) == 4 and 2000 <= int(word) <= 2026:
                score += 1.0
                break
        domain_keywords = {
            "revenue": 1.5,
            "employee": 1.3,
            "market": 1.2,
            "strategy": 1.1,
            "partnership": 1.2,
            "acquisition": 1.3,
            "leadership": 1.1,
            "technology": 1.0,
            "compliance": 1.0,
            "security": 1.0,
            "expansion": 1.1,
            "growth": 1.1,
            "risk": 1.0,
        }
        for keyword, boost in domain_keywords.items():
            if keyword.lower() in query.lower():
                score += boost
                break
        if len(query.split()) < 3:
            score -= 0.5
        query_terms = set(query.lower().split())
        score += min(len(query_terms) / 10, 1.0)
        return max(score, 0.1)

    def _deduplicate_queries(
        self, queries: list[QueryWithMetadata]
    ) -> list[QueryWithMetadata]:
        kept = []
        seen_terms = []
        for q in queries:
            query_terms = set(q.query.lower().split())
            is_duplicate = False
            for seen in seen_terms:
                intersection = len(query_terms & seen)
                union = len(query_terms | seen)
                jaccard = intersection / union if union > 0 else 0
                if jaccard > 0.7:
                    is_duplicate = True
                    break
            if not is_duplicate:
                kept.append(q)
                seen_terms.append(query_terms)
        return kept

    def select(
        self,
        candidates: list[QueryWithMetadata],
        per_domain_limits: dict[str, int] | None = None,
    ) -> NormalizedQueryPlan:
        limits = per_domain_limits or self.DOMAIN_LIMITS
        candidates = self._deduplicate_queries(candidates)

        by_domain: dict[str, list[QueryWithMetadata]] = {}
        for q in candidates:
            by_domain.setdefault(q.domain, []).append(q)

        selected: list[QueryWithMetadata] = []
        per_domain_counts: dict[str, int] = {}

        for domain in sorted(limits.keys()):
            if domain not in by_domain:
                per_domain_counts[domain] = 0
                continue
            domain_queries = by_domain[domain]
            scored = sorted(
                [
                    (self._compute_bm25_score(q.query, self.company_name), q)
                    for q in domain_queries
                ],
                key=lambda x: x[0],
                reverse=True,
            )
            limit = limits[domain]
            for _score, q in scored[:limit]:
                selected.append(q)
                per_domain_counts[domain] = per_domain_counts.get(domain, 0) + 1

        selected.sort(key=lambda q: (q.domain, q.query))
        return NormalizedQueryPlan(
            queries=selected,
            total_candidates=len(candidates),
            budget_used=len(selected),
            per_domain_counts=per_domain_counts,
        )


def build_query_generator_prompt(
    company_name: str, domains: list[str], current_year: int | None = None
) -> str:
    year = current_year or datetime.now().year
    return f"""You are a Search Query Generation Specialist for {company_name}.
Current year: {year}
Research domains: {", ".join(domains)}

Generate 3-5 diverse, targeted search queries for each domain.
Include company name "{company_name}" and year where relevant.
Output matching CandidateQueries schema: {{"domain_queries": {{"firmographics": [...], ...}}}}"""


class QueryGeneratorFactory:
    """Factory for creating the QueryGeneratorAgent."""

    @staticmethod
    def create_query_generator_agent(
        company_name: str,
        depth: str = "deep",
        current_year: int | None = None,
    ) -> LlmAgent:
        domains = sorted(Bm25QuerySelector.DOMAIN_LIMITS)
        instruction = build_query_generator_prompt(company_name, domains, current_year)
        safety_config = get_safety_config_for_agent("QueryGeneratorAgent")

        return RetryingLlmAgent(
            name="QueryGeneratorAgent",
            model=Gemini(model=settings.GEMINI_MODEL, retry_options=retry_config),
            instruction=instruction,
            tools=[],
            output_key="query_generator_output",
            output_schema=CandidateQueries,
            include_contents="none",
            description="Generates search queries for all research domains",
            generate_content_config=safety_config,
            before_model_callback=before_model_callback,
            after_model_callback=after_model_callback,
            before_agent_callback=before_agent_callback,
            after_agent_callback=after_agent_callback,
            before_tool_callback=before_tool_callback,
            after_tool_callback=after_tool_callback,
        )


__all__ = [
    "QueryGeneratorFactory",
    "CandidateQueries",
    "QueryWithMetadata",
    "NormalizedQueryPlan",
    "Bm25QuerySelector",
    "build_query_generator_prompt",
]
