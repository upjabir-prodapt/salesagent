"""BM25-based query selection and ranking."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .schemas import NormalizedQueryPlan, QueryWithMetadata


class Bm25QuerySelector:
    """Select top N queries using BM25-like ranking."""

    # Per-domain query limits (distribute budget)
    DOMAIN_LIMITS = {
        "firmographics": 4,
        "geographic": 3,
        "executive": 4,
        "strategy": 3,
        "compliance": 3,
        "market": 3,
        "ecosystem": 3,
        "tech_stack": 3,
        "procurement": 2,
        "growth_signals": 3,
        "risk_signals": 3,
        "campaign_signals": 2,
    }

    TOTAL_BUDGET = 40

    def __init__(self, company_name: str):
        self.company_name = company_name
        self._query_cache: dict[str, float] = {}

    def _compute_bm25_score(self, query: str, company_name: str) -> float:
        """Simple BM25-like scoring: diversity + specificity."""
        # Boost specificity: company name + year
        score = 0.0

        if company_name.lower() in query.lower():
            score += 2.0

        # Year boost (year specificity is valuable)
        for word in query.split():
            if word.isdigit() and len(word) == 4 and 2000 <= int(word) <= 2026:
                score += 1.0
                break

        # Domain-specific keywords boost
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

        # Penalize very short queries (vague)
        if len(query.split()) < 3:
            score -= 0.5

        # Bonus for more unique terms (less overlapping)
        query_terms = set(query.lower().split())
        unique_term_count = len(query_terms)
        score += min(unique_term_count / 10, 1.0)

        return max(score, 0.1)

    def _deduplicate_queries(
        self, queries: list[QueryWithMetadata]
    ) -> list[QueryWithMetadata]:
        """Remove near-duplicates by Jaccard similarity."""
        kept = []
        seen_terms = []

        for q in queries:
            query_terms = set(q.query.lower().split())

            # Check against already kept queries
            is_duplicate = False
            for seen in seen_terms:
                # Jaccard similarity > 0.7 = likely duplicate
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
        self, candidates: list[QueryWithMetadata], per_domain_limits: dict[str, int] | None = None
    ) -> NormalizedQueryPlan:
        """Select top queries respecting per-domain limits."""
        limits = per_domain_limits or self.DOMAIN_LIMITS

        # Deduplicate
        candidates = self._deduplicate_queries(candidates)

        # Group by domain
        by_domain: dict[str, list[QueryWithMetadata]] = {}
        for q in candidates:
            if q.domain not in by_domain:
                by_domain[q.domain] = []
            by_domain[q.domain].append(q)

        # Score and rank within each domain
        selected: list[QueryWithMetadata] = []
        per_domain_counts: dict[str, int] = {}

        for domain in sorted(limits.keys()):
            if domain not in by_domain:
                per_domain_counts[domain] = 0
                continue

            domain_queries = by_domain[domain]

            # Score each query
            scored = []
            for q in domain_queries:
                score = self._compute_bm25_score(q.query, self.company_name)
                scored.append((score, q))

            # Sort by score descending
            scored.sort(key=lambda x: x[0], reverse=True)

            # Take top N for this domain
            limit = limits[domain]
            for score, q in scored[:limit]:
                selected.append(q)
                per_domain_counts[domain] = per_domain_counts.get(domain, 0) + 1

        # Final sort by domain + score for consistency
        selected.sort(key=lambda q: (q.domain, q.query))

        return NormalizedQueryPlan(
            queries=selected,
            total_candidates=len(candidates),
            budget_used=len(selected),
            per_domain_counts=per_domain_counts,
        )
