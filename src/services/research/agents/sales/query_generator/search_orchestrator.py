"""Search orchestration: execute queries, cache results, manage costs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ......core.logging_config import logger
from ....search_cache import SearchCacheService
from ....cost import CostAnalyzer


@dataclass
class SearchExecution:
    """Result of search execution."""

    query: str
    domain: str
    results: dict[str, Any]
    from_cache: bool
    cached_at: str | None = None


@dataclass
class SearchOrchestrationResult:
    """Complete result of search orchestration."""

    total_queries: int
    executed_queries: int
    cached_queries: int
    search_results: list[SearchExecution]
    cost_analysis: dict[str, Any] | None = None


class SearchOrchestrator:
    """Orchestrate search execution with caching and cost tracking."""

    def __init__(self, company_name: str):
        self.company_name = company_name
        self.cache_service = SearchCacheService()
        self.cost_analyzer = CostAnalyzer()
        self.search_count = 0

    def execute_searches(
        self, queries: list[dict[str, Any]]
    ) -> SearchOrchestrationResult:
        """Execute searches with caching."""
        logger.info(f"Starting search orchestration for {self.company_name}")

        # Extract just query strings
        query_strings = [q.get("query", "") for q in queries if q.get("query")]

        # Check cache for existing queries
        cached_searches = self.cache_service.get_cached_searches(self.company_name)

        # Determine which queries need execution
        uncached_queries = []
        cached_query_map = {}

        if cached_searches:
            for query_str, cache_data in cached_searches.items():
                cached_query_map[query_str] = cache_data

        # Separate cached vs uncached
        results: list[SearchExecution] = []
        queries_to_execute = []

        for q in queries:
            query_str = q.get("query", "")
            domain = q.get("domain", "unknown")

            if query_str in cached_query_map:
                # Use cached result
                cache_data = cached_query_map[query_str]
                results.append(
                    SearchExecution(
                        query=query_str,
                        domain=domain,
                        results=cache_data.get("results", {}),
                        from_cache=True,
                        cached_at=str(cache_data.get("cached_at")),
                    )
                )
                logger.debug(f"Using cached result for: {query_str}")
            else:
                # Need to execute
                queries_to_execute.append({"query": query_str, "domain": domain})

        # Execute uncached queries
        for q in queries_to_execute:
            query_str = q.get("query", "")
            domain = q.get("domain", "unknown")

            # In a real implementation, this would call GoogleSearchAgentTool
            # For now, we'll mock the execution
            search_results = self._execute_single_search(query_str)
            self.search_count += 1

            # Cache the results
            self.cache_service.cache_search_results(
                self.company_name, query_str, search_results, domain
            )

            results.append(
                SearchExecution(
                    query=query_str,
                    domain=domain,
                    results=search_results,
                    from_cache=False,
                )
            )

        logger.info(
            f"Search orchestration complete: {len(results)} results "
            f"({len(results) - len(queries_to_execute)} cached, "
            f"{len(queries_to_execute)} executed)"
        )

        return SearchOrchestrationResult(
            total_queries=len(queries),
            executed_queries=len(queries_to_execute),
            cached_queries=len(results) - len(queries_to_execute),
            search_results=results,
        )

    def _execute_single_search(self, query: str) -> dict[str, Any]:
        """Mock search execution. In production, calls GoogleSearchAgentTool."""
        logger.debug(f"Executing search: {query}")
        # Return mock search result
        return {
            "snippets": [
                f"Result for: {query}",
                "Information retrieved from search",
            ],
            "sources": ["https://example.com/result1", "https://example.com/result2"],
            "query": query,
        }

    def analyze_costs(
        self, model: str, input_tokens: int, output_tokens: int
    ) -> dict[str, Any]:
        """Analyze costs including search counts."""
        cost_analysis = self.cost_analyzer.analyze(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            search_count=self.search_count,
        )
        return cost_analysis.to_dict()
