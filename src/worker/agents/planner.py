"""QueryPlanner: generates and BM25-selects search queries for a company.

Replaces agents/keyword_agent.py's QueryGeneratorFactory + RetryingLlmAgent
wiring. The BM25 selection logic (Bm25QuerySelector) is moved in verbatim
-- only the surrounding I/O is retyped from raw ADK session state into
ResearchRequest -> QueryPlan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from google.adk.agents import LlmAgent
from pydantic import BaseModel, ConfigDict, Field

from src.shared.config import settings
from src.worker.agents.base import AdkAgentStep, RetryPolicy
from src.worker.agents.models import Query, QueryPlan, ResearchRequest
from src.worker.agents.safety import get_safety_config_for_agent
from src.worker.model import RegionalGemini, retry_config


class _QueryWithMetadata(BaseModel):
    """Single search query with domain metadata (internal to the planner)."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="The search query")
    domain: str = Field(..., description="Domain (e.g., 'firmographics', 'market')")
    year: int | None = Field(None, description="Year specificity if applicable")


class CandidateQueries(BaseModel):
    """Output schema for QueryPlanner's LlmAgent (ADK output_schema)."""

    domain_queries: dict[str, list[str]] = Field(
        ..., description="Mapping of domain -> list of candidate queries"
    )

    def to_flat_list(self) -> list[_QueryWithMetadata]:
        result: list[_QueryWithMetadata] = []
        for domain, queries in self.domain_queries.items():
            for query in queries:
                year = None
                for word in query.split():
                    if word.isdigit() and len(word) == 4 and 2000 <= int(word) <= 2026:
                        year = int(word)
                        break
                result.append(_QueryWithMetadata(query=query, domain=domain, year=year))
        return result


class Bm25QuerySelector:
    """Select top N queries using BM25-like ranking and deduplication.

    Moved in verbatim from agents/keyword_agent.py -- the selection logic
    is unchanged, only its input/output types are internal to this module.
    """

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
        self, queries: list[_QueryWithMetadata]
    ) -> list[_QueryWithMetadata]:
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
        candidates: list[_QueryWithMetadata],
        per_domain_limits: dict[str, int] | None = None,
    ) -> list[_QueryWithMetadata]:
        limits = per_domain_limits or self.DOMAIN_LIMITS
        candidates = self._deduplicate_queries(candidates)

        by_domain: dict[str, list[_QueryWithMetadata]] = {}
        for q in candidates:
            by_domain.setdefault(q.domain, []).append(q)

        selected: list[_QueryWithMetadata] = []
        for domain in sorted(limits.keys()):
            if domain not in by_domain:
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

        selected.sort(key=lambda q: (q.domain, q.query))
        return selected


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


class QueryPlanner(AdkAgentStep[ResearchRequest, QueryPlan]):
    """Generates and BM25-selects search queries for one company.

    Output is a typed QueryPlan (no shared session state) consumed
    directly by SearchExecutor.
    """

    name = "QueryPlanner"

    def __init__(
        self, *, model: str | None = None, retry: RetryPolicy | None = None
    ) -> None:
        self._model = model or settings.GEMINI_MODEL
        if retry is not None:
            self.retry = retry

    def build_agent(self) -> LlmAgent:
        return LlmAgent(
            name=self.name,
            model=RegionalGemini(model=self._model, retry_options=retry_config),
            # The concrete task (company, domains, year) is provided in the
            # per-request user message via to_input() -- this instruction
            # only sets the agent's fixed role.
            instruction="You are a Search Query Generation Specialist.",
            tools=[],
            output_key="query_generator_output",
            output_schema=CandidateQueries,
            include_contents="none",
            description="Generates search queries for all research domains",
            generate_content_config=get_safety_config_for_agent(self.name),
        )

    def to_input(self, request: ResearchRequest) -> str:
        domains = sorted(Bm25QuerySelector.DOMAIN_LIMITS)
        return build_query_generator_prompt(request.company, domains)

    def to_output(self, raw: Any, usage: tuple[int, int]) -> QueryPlan:
        if isinstance(raw, str):
            candidates = CandidateQueries.model_validate_json(raw)
        elif isinstance(raw, dict):
            candidates = CandidateQueries.model_validate(raw)
        else:
            candidates = raw  # already a CandidateQueries instance

        selector = Bm25QuerySelector(self._company)
        selected = selector.select(candidates.to_flat_list())
        return QueryPlan(
            company=self._company,
            queries=tuple(Query(text=q.query, domain=q.domain) for q in selected),
        )

    async def execute(self, request: ResearchRequest) -> QueryPlan:
        # to_output() needs the company name but AdkAgentStep's contract
        # only passes (raw, usage); stash it for the duration of this call.
        self._company = request.company
        return await super().execute(request)


__all__ = [
    "Bm25QuerySelector",
    "CandidateQueries",
    "QueryPlanner",
    "build_query_generator_prompt",
]
