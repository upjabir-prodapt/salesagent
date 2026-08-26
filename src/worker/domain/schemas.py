"""Domain schemas for research queries, alignment, and reports."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# --- Section: Query Generation ---


class QueryWithMetadata(BaseModel):
    """Single search query with domain metadata."""

    model_config = ConfigDict(frozen=True)

    query: str = Field(..., description="The search query")
    domain: str = Field(..., description="Domain (e.g., 'firmographics', 'market')")
    year: int | None = Field(None, description="Year specificity if applicable")


class CandidateQueries(BaseModel):
    """Output schema from QueryGeneratorAgent."""

    domain_queries: dict[str, list[str]] = Field(
        ..., description="Mapping of domain -> list of candidate queries"
    )

    def to_flat_list(self) -> list[QueryWithMetadata]:
        """Convert domain queries to flat list with metadata."""
        result = []
        for domain, queries in self.domain_queries.items():
            for query in queries:
                year = None
                for word in query.split():
                    if word.isdigit() and len(word) == 4 and 2000 <= int(word) <= 2026:
                        year = int(word)
                        break
                result.append(QueryWithMetadata(query=query, domain=domain, year=year))
        return result


class NormalizedQueryPlan(BaseModel):
    """BM25-selected query plan ready for search execution."""

    queries: list[QueryWithMetadata] = Field(..., description="Selected queries")
    total_candidates: int = Field(..., description="Total candidates generated")
    budget_used: int = Field(..., description="Queries selected (<= 30)")
    per_domain_counts: dict[str, int] = Field(..., description="Count per domain")


# --- Section: Colt Alignment ---


class ColtAlignmentMapping(BaseModel):
    """Single challenge-to-solution mapping row."""

    challenge_or_priority: str = Field(
        ..., description="Specific target company challenge or priority"
    )
    colt_solution: str = Field(
        ..., description="Colt solution enabler that addresses this challenge"
    )
    alignment_justification: str = Field(
        ..., description="Commercial pitch and value proposition"
    )


class UseCaseRecommendation(BaseModel):
    """Recommended sales meeting narrative."""

    use_case: str = Field(description="Meeting type or context")
    recommended_narrative: str = Field(description="Strategic narrative to lead with")


class StrategicOpportunitySummary(BaseModel):
    """Executive opportunity brief."""

    summary: str = Field(description="Executive-level Why Colt? Why Now?")
    hooks: list[str] = Field(default_factory=list, description="Opening hooks")
    executive_narratives: list[str] = Field(
        default_factory=list, description="C-suite storylines"
    )
    regulatory_triggers: list[str] = Field(
        default_factory=list, description="Regulatory triggers"
    )
    ai_urgency: list[str] = Field(
        default_factory=list, description="AI network dependency"
    )
    competitive_displacement_angles: list[str] = Field(
        default_factory=list, description="Displacement angles"
    )
    colt_differentiation: list[str] = Field(
        default_factory=list, description="Colt differentiators & SLAs"
    )
    use_case_recommendations: list[UseCaseRecommendation] = Field(
        default_factory=list, description="Meeting recommendations"
    )


class ColtAlignmentOutput(BaseModel):
    """Output schema from AlignmentAnalyst."""

    alignment_mappings: list[ColtAlignmentMapping] = Field(
        ..., description="Challenge-to-solution rows"
    )
    strategic_opportunity: StrategicOpportunitySummary = Field(
        ..., description="Strategic summary"
    )


__all__ = [
    "QueryWithMetadata",
    "CandidateQueries",
    "NormalizedQueryPlan",
    "ColtAlignmentMapping",
    "UseCaseRecommendation",
    "StrategicOpportunitySummary",
    "ColtAlignmentOutput",
]
