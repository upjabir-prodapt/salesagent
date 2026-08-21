"""Schemas for query generation and selection."""

from pydantic import BaseModel, Field


class QueryWithMetadata(BaseModel):
    """Single search query with metadata."""

    query: str = Field(..., description="The search query")
    domain: str = Field(..., description="Domain (e.g., 'firmographics', 'market')")
    year: int | None = Field(None, description="Year specificity if applicable")

    class Config:
        frozen = True


class CandidateQueries(BaseModel):
    """Output from query generator agent."""

    domain_queries: dict[str, list[str]] = Field(
        ..., description="Mapping of domain -> list of queries"
    )

    def to_flat_list(self) -> list[QueryWithMetadata]:
        """Convert to flat list with metadata."""
        result = []
        for domain, queries in self.domain_queries.items():
            for query in queries:
                # Simple year extraction from query
                year = None
                for word in query.split():
                    if word.isdigit() and len(word) == 4 and 2000 <= int(word) <= 2026:
                        year = int(word)
                        break
                result.append(QueryWithMetadata(query=query, domain=domain, year=year))
        return result


class NormalizedQueryPlan(BaseModel):
    """Final selected queries ready for searching."""

    queries: list[QueryWithMetadata] = Field(
        ..., description="Selected queries for searching"
    )
    total_candidates: int = Field(..., description="Total candidates generated")
    budget_used: int = Field(..., description="Queries selected (should be <= 40)")
    per_domain_counts: dict[str, int] = Field(
        ..., description="Query count per domain"
    )
