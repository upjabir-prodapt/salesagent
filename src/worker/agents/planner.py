"""QueryPlanner: generates and BM25-selects search queries for a company.

Replaces agents/keyword_agent.py's QueryGeneratorFactory + RetryingLlmAgent
wiring. The BM25 selection logic (Bm25QuerySelector) is moved in verbatim
-- only the surrounding I/O is retyped from raw ADK session state into
ResearchRequest -> QueryPlan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from google.adk.agents import LlmAgent
from pydantic import BaseModel, ConfigDict, Field

from src.shared.config import settings
from src.worker.agents.base import AdkAgentStep, InvalidOutputError, RetryPolicy
from src.worker.agents.models import Query, QueryPlan, ResearchRequest
from src.worker.agents.safety import get_safety_config_for_agent
from src.worker.model import RegionalGemini, retry_config

# One-sentence description per research domain, used both to enrich the
# generation prompt (so the model knows what each domain actually means,
# not just its slug) and as the single source of truth for the domain
# enum used by DomainQueryGroup below. Keys must match
# Bm25QuerySelector.DOMAIN_LIMITS and src.worker.agents.search's
# DOMAIN_SLUG_TO_OUTPUT_KEY exactly.
DOMAIN_DESCRIPTIONS: dict[str, str] = {
    "firmographics": (
        "Core company facts: revenue, employee count, ownership structure, "
        "subsidiaries, financial performance, and market capitalization."
    ),
    "geographic": (
        "Physical footprint and expansion: headquarters, regional offices, "
        "data centers, manufacturing sites, and new-market entry plans."
    ),
    "executive": (
        "Leadership: C-suite and board members, recent appointments/"
        "departures, and their public statements on strategy or technology."
    ),
    "strategy": (
        "Corporate strategy: digital transformation initiatives, stated "
        "business priorities, multi-year plans, and strategic pivots."
    ),
    "compliance": (
        "Regulatory and legal posture: industry regulations, certifications, "
        "data-privacy obligations, audits, and compliance incidents."
    ),
    "market": (
        "Competitive and market position: market share, key competitors, "
        "industry trends, and analyst commentary."
    ),
    "ecosystem": (
        "Partnerships and alliances: technology/vendor partnerships, "
        "channel relationships, joint ventures, and integration ecosystems."
    ),
    "tech_stack": (
        "Technology footprint: cloud providers, network/connectivity "
        "vendors, enterprise software, and infrastructure modernization "
        "projects -- the primary signal for Colt's connectivity offerings."
    ),
    "procurement": (
        "Purchasing behavior: vendor selection criteria, RFP/tender "
        "activity, sourcing strategy, and existing supplier relationships."
    ),
    "growth_signals": (
        "Expansion indicators: mergers and acquisitions, funding rounds, "
        "hiring trends, and new product or market launches."
    ),
    "risk_signals": (
        "Risk indicators: security incidents, outages, litigation, "
        "financial distress, and negative press coverage."
    ),
    "campaign_signals": (
        "Marketing and public visibility: advertising campaigns, event "
        "sponsorships, press releases, and brand positioning."
    ),
}


class _QueryWithMetadata(BaseModel):
    """Single search query with domain metadata (internal to the planner)."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="The search query")
    domain: str = Field(..., description="Domain (e.g., 'firmographics', 'market')")
    year: int | None = Field(None, description="Year specificity if applicable")


DomainName = Literal[
    "firmographics",
    "geographic",
    "executive",
    "strategy",
    "compliance",
    "market",
    "ecosystem",
    "tech_stack",
    "procurement",
    "growth_signals",
    "risk_signals",
    "campaign_signals",
]

if set(DomainName.__args__) != set(DOMAIN_DESCRIPTIONS):  # pragma: no cover
    raise RuntimeError("DomainName must cover every DOMAIN_DESCRIPTIONS key exactly")


class DomainQueryGroup(BaseModel):
    """Candidate search queries generated for exactly one research domain.

    Modeled as an explicit array-of-objects (list[DomainQueryGroup]) rather
    than an open-ended dict[str, list[str]] mapping. Gemini's constrained
    structured-output decoding follows an array-of-typed-objects schema
    (with a closed `domain` enum) far more reliably than an
    additionalProperties-style dict schema: a dict schema only requires
    *some* object be returned and lets the model emit an empty `{}` and
    still satisfy validation, whereas an array schema paired with a
    required, enumerated `domain` field per item gives the decoder an
    explicit, closed set of values it must attempt to produce. This
    directly addresses a live bug (2026-08-29) where Gemini 3.5 Flash
    spent its entire response budget on internal "thinking" and then
    emitted a syntactically valid but empty `{"domain_queries": {}}` under
    the old dict-shaped schema (finish_reason=STOP, candidates_token_count
    as low as 11) -- see QueryPlanner.validate() for the runtime safety
    net that remains in place regardless.
    """

    domain: DomainName = Field(
        ..., description="Which research domain this group of queries covers."
    )
    queries: list[str] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="3-5 diverse, targeted search queries for this domain.",
    )


class CandidateQueries(BaseModel):
    """Output schema for QueryPlanner's LlmAgent (ADK output_schema)."""

    domain_query_groups: list[DomainQueryGroup] = Field(
        ...,
        description=(
            "One entry per research domain listed in the prompt, each "
            "with 3-5 candidate search queries for that domain."
        ),
    )

    def to_flat_list(self) -> list[_QueryWithMetadata]:
        result: list[_QueryWithMetadata] = []
        for group in self.domain_query_groups:
            for query in group.queries:
                year = None
                for word in query.split():
                    if word.isdigit() and len(word) == 4 and 2000 <= int(word) <= 2026:
                        year = int(word)
                        break
                result.append(
                    _QueryWithMetadata(query=query, domain=group.domain, year=year)
                )
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
    domain_lines = "\n".join(
        f"- {domain}: {DOMAIN_DESCRIPTIONS[domain]}" for domain in domains
    )
    return f"""You are a Google Search Query Generation Specialist working for \
Colt Technology Services' sales research team. Your queries are the \
foundation of a Strategic Account Brief that a Colt sales rep will use to \
identify and pitch a concrete sales opportunity at {company_name} -- every \
query you write should surface facts that later help map {company_name}'s \
real challenges and priorities to a Colt solution (network connectivity, \
SD-WAN/SASE, cloud on-ramps, colocation, etc.).

Target company: {company_name}
Current year: {year}

Generate 3-5 diverse, targeted search queries for EACH of the following \
research domains. Use each domain's description to decide what the \
queries should actually investigate -- do not just repeat the domain name. \
Prioritise queries likely to surface a business pain point, technology \
gap, or growth trigger that Colt could address.

Research domains:
{domain_lines}

Include company name "{company_name}" and year where relevant.

## Required Output Structure

Return a JSON object matching this exact structure (this is the \
CandidateQueries schema, also enforced via response_schema):

{{
  "domain_query_groups": [
    {{"domain": "<one of the domain names listed above>", "queries": ["<query 1>", "<query 2>", "<query 3>", ...]}},
    ...
  ]
}}

Requirements:
- Include exactly one entry per domain listed above -- all {len(domains)} domains, no more, no fewer, no duplicates.
- Each entry's "queries" list must contain 3 to 5 distinct search query strings.
- Never return an empty "domain_query_groups" list or omit a domain -- \
every domain must have its own populated entry with real, usable queries."""


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
            instruction=(
                "You are a Google Search Query Generation Specialist for "
                "Colt Technology Services' sales research team. You generate "
                "search queries that will surface facts feeding into a "
                "Strategic Account Brief used to identify a sales opportunity."
            ),
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

    def validate(self, result: QueryPlan) -> None:
        """Reject a structurally-valid-but-empty plan and trigger a retry.

        Observed live (2026-08-29): Gemini 3.5 Flash occasionally returns
        a syntactically valid but empty `{"domain_queries": {}}` under
        output_schema (finish_reason=STOP, candidates_token_count as low
        as 11, with thoughts_token_count in the 1000+ range consuming the
        whole response budget on internal reasoning before emitting any
        schema content). AdkAgentStep.execute()'s "not empty" check only
        rejects a None/blank-string output_key value, so a valid-but-empty
        dict passes through silently and SearchExecutor then has zero
        queries to run -- the report is compiled with
        "(no domain findings available)" and no real research content.
        """
        if not result.queries:
            raise InvalidOutputError(
                f"{self.name} produced an empty query plan (0 queries) "
                "for company="
                f"{result.company!r} -- likely a thinking-budget/schema "
                "interaction; retrying.",
                agent_name=self.name,
            )

    async def execute(self, request: ResearchRequest) -> QueryPlan:
        # to_output() needs the company name but AdkAgentStep's contract
        # only passes (raw, usage); stash it for the duration of this call.
        self._company = request.company
        return await super().execute(request)


__all__ = [
    "Bm25QuerySelector",
    "CandidateQueries",
    "DOMAIN_DESCRIPTIONS",
    "DomainName",
    "DomainQueryGroup",
    "QueryPlanner",
    "build_query_generator_prompt",
]
