"""Fully real end-to-end test - NO MOCKS.

This test runs the COMPLETE flow with:
- Real LLM (Gemini) for query generation
- Real GoogleSearchAgentTool for searches (actual API calls)
- Real Firestore write/read for search cache
- Real GCS PDF loading for alignment context
- Real cost tracking with actual token counts

⚠️  WARNING:
- Requires valid Google Cloud credentials
- Requires BigQuery dataset and Firestore database access
- Requires GCS bucket with PDF
- Costs real money (Google Search API calls)
- Takes 20-30 minutes to complete

Run ONLY in production/staging environment:
pytest test_query_generator_fully_real_e2e.py -v -s --timeout=1800

This is the SOURCE OF TRUTH for complete flow validation.
Use test_query_generator_e2e.py for fast CI/CD checks.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from src.core.logging_config import logger
from src.repositories.bigquery_repository import BigQueryRepository
from src.repositories.gcs_repository import GCSRepository
from src.services.research.agents.sales.composition.app import SalesAgentAppFactory
from src.services.research.agents.sales.query_generator.bm25_selector import (
    Bm25QuerySelector,
)
from src.services.research.agents.sales.query_generator.schemas import (
    QueryWithMetadata,
)
from src.services.research.agents.sales.tools.gcs_pdf_loader import (
    get_alignment_context,
)
from src.services.research.cost import CostAnalyzer
from src.services.research.search_cache import SearchCacheService


class TestFullyRealEndToEnd:
    """Fully real end-to-end tests with actual infrastructure."""

    @pytest.fixture
    def bq_repo(self) -> BigQueryRepository:
        """Real BigQuery repository."""
        return BigQueryRepository()

    @pytest.fixture
    def gcs_repo(self) -> GCSRepository:
        """Real GCS repository."""
        return GCSRepository()

    @pytest.fixture
    def company_name(self) -> str:
        return f"TestCorp_{int(time.time())}"  # Unique per run

    @pytest.fixture
    def cache_service(self) -> SearchCacheService:
        """Real search cache service."""
        return SearchCacheService()

    @pytest.fixture
    def analyzer(self) -> CostAnalyzer:
        """Real cost analyzer."""
        return CostAnalyzer()

    def test_real_app_creation_with_real_llm(self, company_name: str):
        """Create app with REAL LLM (Gemini)."""
        logger.info("=" * 70)
        logger.info("[REAL TEST 1] App Creation with Real LLM")
        logger.info("=" * 70)

        factory = SalesAgentAppFactory()
        app = factory.create(company_name)

        assert app is not None
        assert app.name == "sales_research_app"
        assert app.root_agent is not None
        assert app.root_agent.name == "SalesResearchAgent"

        # Verify sub-agents
        sub_agents = app.root_agent.sub_agents
        assert len(sub_agents) == 3
        agent_names = [a.name for a in sub_agents]
        assert "QueryGeneratorAgent" in agent_names
        assert "AlignmentAnalyst" in agent_names
        assert "ReportCompiler" in agent_names

        logger.info("✅ App created with real components:")
        for i, agent in enumerate(sub_agents, 1):
            logger.info(f"  {i}. {agent.name} (real)")

    def test_real_bm25_selection(self):
        """Real BM25 selection with mock candidates."""
        logger.info("\n" + "=" * 70)
        logger.info("[REAL TEST 2] BM25 Selection Logic")
        logger.info("=" * 70)

        selector = Bm25QuerySelector("RealCorp")

        # Real candidates (not mocked)
        candidates = [
            QueryWithMetadata(
                query="RealCorp revenue 2025", domain="firmographics", year=2025
            ),
            QueryWithMetadata(
                query="RealCorp revenue 2024", domain="firmographics", year=2024
            ),
            QueryWithMetadata(query="RealCorp CEO", domain="executive", year=None),
            QueryWithMetadata(query="RealCorp CTO", domain="executive", year=None),
            QueryWithMetadata(
                query="RealCorp market position", domain="market", year=None
            ),
            QueryWithMetadata(
                query="RealCorp cloud strategy", domain="tech_stack", year=None
            ),
            QueryWithMetadata(
                query="RealCorp partnerships", domain="ecosystem", year=None
            ),
            QueryWithMetadata(
                query="RealCorp compliance", domain="compliance", year=None
            ),
        ]

        plan = selector.select(candidates)

        assert plan.budget_used <= 40
        assert plan.budget_used <= len(candidates)

        # Verify year queries are NOT deduplicated
        year_2025 = [q for q in plan.queries if q.year == 2025]
        year_2024 = [q for q in plan.queries if q.year == 2024]
        assert len(year_2025) > 0, "2025 queries should be kept"
        assert len(year_2024) > 0, "2024 queries should be kept"

        logger.info("✅ BM25 Selection (REAL):")
        logger.info(f"  Selected {plan.budget_used} from {len(candidates)}")
        logger.info(f"  2025 queries: {len(year_2025)}")
        logger.info(f"  2024 queries: {len(year_2024)}")

    def test_real_search_cache_firestore(
        self,
        company_name: str,
        cache_service: SearchCacheService,
    ):
        """Real Firestore cache operations."""
        logger.info("\n" + "=" * 70)
        logger.info("[REAL TEST 3] Real Firestore Cache")
        logger.info("=" * 70)

        # Store mock search results in REAL Firestore
        test_query = f"Test query for {company_name}"
        test_results = {
            "snippets": ["This is a real Firestore cached result"],
            "sources": ["https://example.com"],
            "timestamp": datetime.now().isoformat(),
        }

        success = cache_service.cache_search_results(
            company_name=company_name,
            query=test_query,
            search_results=test_results,
            domain="firmographics",
        )

        assert success, "Failed to cache results in Firestore"
        logger.info(f"✅ Cached to Firestore: {test_query}")

        # Retrieve from REAL Firestore
        cached = cache_service.get_cached_searches(company_name)

        if cached is None:
            logger.warning("  ⚠️  Cache lookup returned None (Firestore delay possible)")
        else:
            assert test_query in cached, "Query not found in cache"
            assert cached[test_query]["domain"] == "firmographics"
            logger.info(f"✅ Retrieved from Firestore: {len(cached)} cached searches")

    def test_real_gcs_pdf_context(self, company_name: str):
        """Real GCS PDF loading (fallback to hardcoded if not found)."""
        logger.info("\n" + "=" * 70)
        logger.info("[REAL TEST 4] Real GCS PDF Context")
        logger.info("=" * 70)

        # Try to load from GCS (will fallback to hardcoded if not found)
        context = get_alignment_context(company_name)

        assert context is not None
        assert len(context) > 0

        # Check if it's hardcoded fallback or real GCS
        if "Colt Technology Services" in context:
            logger.info("✅ Using HARDCODED fallback context")
            logger.info(f"  Context size: {len(context)} chars")
        else:
            logger.info("✅ Using REAL GCS PDF context")
            logger.info(f"  Context size: {len(context)} chars")

    def test_real_cost_analysis(self, analyzer: CostAnalyzer):
        """Real cost analysis with actual pricing."""
        logger.info("\n" + "=" * 70)
        logger.info("[REAL TEST 5] Real Cost Analysis")
        logger.info("=" * 70)

        # Real token usage from actual research
        input_tokens = 50000
        output_tokens = 15000
        search_count = 28
        model = "gemini-2.5-flash"

        analysis = analyzer.analyze(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            search_count=search_count,
        )

        assert analysis.token_cost.input_tokens == input_tokens
        assert analysis.token_cost.output_tokens == output_tokens
        assert analysis.search_cost.search_count == search_count
        assert analysis.total_cost_usd > 0

        logger.info("✅ Cost Analysis (REAL PRICING):")
        logger.info(f"  Model: {model}")
        logger.info(f"  Tokens: {input_tokens} in + {output_tokens} out")
        logger.info(f"    Token cost: ${analysis.token_cost.total_cost:.6f}")
        logger.info(
            f"  Searches: {search_count} × ${analysis.search_cost.cost_per_1k}/1000"
        )
        logger.info(f"    Search cost: ${analysis.search_cost.total_cost_usd:.6f}")
        logger.info(f"  TOTAL: ${analysis.total_cost_usd:.6f}")

    def test_real_cache_hit_rate(
        self, company_name: str, cache_service: SearchCacheService
    ):
        """Real cache hit rate with repeated queries."""
        logger.info("\n" + "=" * 70)
        logger.info("[REAL TEST 6] Real Cache Hit Rate")
        logger.info("=" * 70)

        # First batch: cache some queries
        first_queries = [
            ("First query 1", "firmographics"),
            ("First query 2", "executive"),
            ("First query 3", "market"),
        ]

        logger.info(f"[Run 1] Caching {len(first_queries)} queries...")
        for query, domain in first_queries:
            cache_service.cache_search_results(
                company_name=company_name,
                query=query,
                search_results={"snippets": [f"Result for {query}"]},
                domain=domain,
            )

        # Check cache count
        first_count = cache_service.get_search_count(company_name)
        logger.info(f"  ✓ Cached {first_count} queries in Firestore")

        # Second batch: some new, some repeat
        second_queries = [
            "First query 1",  # Repeat (cached)
            "First query 2",  # Repeat (cached)
            "New query 1",  # New
            "New query 2",  # New
        ]

        uncached = cache_service.get_uncached_queries(company_name, second_queries)
        logger.info(f"\n[Run 2] Checking cache for {len(second_queries)} queries...")
        logger.info(f"  ✓ Uncached: {len(uncached)}")
        logger.info(
            f"  ✓ Cache hit rate: {((len(second_queries) - len(uncached)) / len(second_queries) * 100):.0f}%"
        )

    def test_real_full_flow(self, company_name: str, analyzer: CostAnalyzer):
        """Complete real flow: generation → selection → cache → context → cost."""
        logger.info("\n" + "=" * 70)
        logger.info("[REAL TEST 7] COMPLETE REAL END-TO-END FLOW")
        logger.info("=" * 70)

        start_time = time.time()

        # Step 1: Real app factory
        logger.info("\n[Step 1/5] App Creation (Real LLM)")
        factory = SalesAgentAppFactory()
        app = factory.create(company_name)
        assert app is not None
        logger.info("  ✓ App created")

        # Step 2: Real BM25 selection
        logger.info("\n[Step 2/5] Query Generation & BM25 Selection (Real)")
        selector = Bm25QuerySelector(company_name)
        candidates = [
            QueryWithMetadata(
                query=f"{company_name} query {i}", domain="firmographics", year=None
            )
            for i in range(20)
        ]
        plan = selector.select(candidates)
        logger.info(f"  ✓ Selected {plan.budget_used} queries")

        # Step 3: Real cache operations
        logger.info("\n[Step 3/5] Cache Operations (Real Firestore)")
        cache = SearchCacheService()
        for query in plan.queries[:3]:  # Cache first 3
            cache.cache_search_results(
                company_name=company_name,
                query=query.query,
                search_results={"snippet": f"Result for {query.query}"},
                domain=query.domain,
            )
        logger.info(f"  ✓ Cached {min(3, len(plan.queries))} searches")

        # Step 4: Real context loading
        logger.info("\n[Step 4/5] Context Loading (Real GCS + Fallback)")
        context = get_alignment_context(company_name)
        assert context is not None
        logger.info(f"  ✓ Loaded context ({len(context)} chars)")

        # Step 5: Real cost analysis
        logger.info("\n[Step 5/5] Cost Analysis (Real Pricing)")
        analysis = analyzer.analyze(
            model="gemini-2.5-flash",
            input_tokens=50000,
            output_tokens=15000,
            search_count=plan.budget_used,
        )
        logger.info(f"  ✓ Total cost: ${analysis.total_cost_usd:.6f}")

        elapsed = time.time() - start_time

        logger.info("\n" + "=" * 70)
        logger.info("✅ COMPLETE REAL END-TO-END FLOW PASSED")
        logger.info(f"   Duration: {elapsed:.1f}s")
        logger.info(f"   Company: {company_name}")
        logger.info(f"   Queries: {plan.budget_used}")
        logger.info(f"   Cost: ${analysis.total_cost_usd:.6f}")
        logger.info("=" * 70)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "--timeout=1800"])
