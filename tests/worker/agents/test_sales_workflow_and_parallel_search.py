"""Unit tests for ParallelSearchAgent and SalesResearchWorkflowAgent."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.worker.agents.keyword_agent import Bm25QuerySelector
from src.worker.agents.search_agent import ParallelSearchAgent
from src.worker.domain.contracts import DOMAIN_OUTPUT_KEYS
from src.worker.domain.schemas import (
    QueryWithMetadata,
)


def test_bm25_selector_30_query_budget():
    selector = Bm25QuerySelector("Acme Corp")
    candidates = []
    for domain in Bm25QuerySelector.DOMAIN_LIMITS:
        for i in range(5):
            candidates.append(
                QueryWithMetadata(
                    query=f"Acme Corp {domain} query {i} 2025", domain=domain
                )
            )

    plan = selector.select(candidates)
    assert plan.budget_used <= 30
    assert len(plan.queries) <= 30
    assert plan.budget_used > 0


def test_parallel_search_agent_extract_queries():
    agent = ParallelSearchAgent()
    state = {
        "company_name": "Acme Corp",
        "query_generator_output": {
            "domain_queries": {
                "firmographics": ["Acme Corp revenue 2025", "Acme Corp employees"],
                "executive": ["Acme Corp CEO Jane Doe"],
            }
        },
    }
    queries = agent._extract_queries(state, "Acme Corp")
    assert len(queries) >= 1
    assert any("revenue" in q.query.lower() for q in queries)


@pytest.mark.asyncio
async def test_parallel_search_agent_run_mocked():
    agent = ParallelSearchAgent()

    # Mock session and state
    state = {
        "company_name": "TestCompany",
        "query_generator_output": {
            "domain_queries": {
                "firmographics": ["TestCompany revenue 2025"],
                "geographic": ["TestCompany offices"],
                "executive": ["TestCompany leadership"],
                "strategy": ["TestCompany strategy 2025"],
                "compliance": ["TestCompany compliance certifications"],
                "market": ["TestCompany market share"],
                "ecosystem": ["TestCompany partnerships"],
                "tech_stack": ["TestCompany cloud infrastructure"],
                "procurement": ["TestCompany procurement process"],
                "growth_signals": ["TestCompany hiring growth"],
                "risk_signals": ["TestCompany regulatory risks"],
                "campaign_signals": ["TestCompany marketing campaigns"],
            }
        },
    }

    mock_ctx = MagicMock()
    mock_ctx.session.state = state
    mock_ctx.invocation_id = "inv-123"
    mock_ctx.branch = "test-branch"

    with (
        patch.object(
            agent,
            "_execute_single_search",
            new_callable=AsyncMock,
            return_value={
                "query": "TestCompany revenue 2025",
                "domain": "firmographics",
                "text": "TestCompany revenue was $500M in 2025.",
                "snippets": ["TestCompany revenue was $500M in 2025."],
                "sources": ["https://example.com/report"],
            },
        ),
        patch(
            "src.worker.agents.search_agent.RedisSearchCacheRepository"
        ) as mock_repo_cls,
    ):
        mock_repo = MagicMock()
        mock_repo.async_get_search = AsyncMock(return_value=None)
        mock_repo.async_set_search = AsyncMock(return_value=True)
        mock_repo_cls.return_value = mock_repo

        events = []
        async for event in agent._run_async_impl(mock_ctx):
            events.append(event)

        assert len(events) >= 1
        # Check all 12 domain output keys are populated
        for key in DOMAIN_OUTPUT_KEYS:
            assert key in state
            assert bool(state[key])
