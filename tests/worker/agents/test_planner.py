"""Tests for QueryPlanner (src/worker/agents/planner.py)."""

from __future__ import annotations

import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from src.worker.agents.base import AgentError, RetryPolicy
from src.worker.agents.models import ResearchRequest
from src.worker.agents.planner import Bm25QuerySelector, QueryPlanner
from src.worker.observers import Observer


class NullObserver(Observer):
    def on_start(self, agent_name, attempt):
        pass

    def on_retry(self, agent_name, attempt, kind, delay):
        pass

    def on_success(self, agent_name, attempt, seconds):
        pass

    def on_failure(self, agent_name, attempt, kind, exc):
        pass


class ScriptedLlm(BaseLlm):
    model: str = "fake-planner"
    payload: str = "{}"

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.payload)]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=20, candidates_token_count=40
            ),
        )


def _domain_queries_payload() -> str:
    return json.dumps(
        {
            "domain_queries": {
                "firmographics": [
                    "Acme Corp revenue 2025",
                    "Acme Corp employee count",
                    "Acme Corp market cap",
                ],
                "tech_stack": [
                    "Acme Corp cloud infrastructure",
                    "Acme Corp cybersecurity vendor",
                ],
            }
        }
    )


@pytest.mark.asyncio
async def test_query_planner_produces_bm25_selected_plan():
    planner = QueryPlanner(retry=RetryPolicy(max_attempts=1))
    planner._model = "fake-planner"

    # Monkeypatch build_agent to inject a scripted LLM without a live model.
    original_build_agent = planner.build_agent

    def build_agent_with_fake_llm():
        agent = original_build_agent()
        agent.model = ScriptedLlm(payload=_domain_queries_payload())
        return agent

    planner.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]

    plan = await planner.run(
        ResearchRequest(job_id="job1", company="Acme Corp"), NullObserver()
    )

    assert plan.company == "Acme Corp"
    assert len(plan.queries) > 0
    domains = {q.domain for q in plan.queries}
    assert domains <= set(Bm25QuerySelector.DOMAIN_LIMITS)
    # Firmographics limit is 3; only 3 candidates given, all should survive
    # dedup and selection.
    firmographics = [q for q in plan.queries if q.domain == "firmographics"]
    assert len(firmographics) <= Bm25QuerySelector.DOMAIN_LIMITS["firmographics"]


@pytest.mark.asyncio
async def test_query_planner_retries_on_empty_domain_queries_then_exhausts():
    """Regression test for a live bug (2026-08-29): Gemini 3.5 Flash
    occasionally returns a syntactically valid but empty
    {"domain_queries": {}} under output_schema (finish_reason=STOP,
    candidates_token_count as low as 11, thinking budget fully consumed).
    This used to pass through silently as a 0-query QueryPlan, leaving
    SearchExecutor with nothing to search and the report compiled with
    "(no domain findings available)". validate() must now reject an empty
    plan so the step retries instead of silently degrading.
    """
    planner = QueryPlanner(retry=RetryPolicy(max_attempts=2, initial_delay=0.001))
    original_build_agent = planner.build_agent

    def build_agent_with_fake_llm():
        agent = original_build_agent()
        agent.model = ScriptedLlm(payload=json.dumps({"domain_queries": {}}))
        return agent

    planner.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]

    with pytest.raises(AgentError):
        await planner.run(
            ResearchRequest(job_id="job1", company="Acme Corp"), NullObserver()
        )


@pytest.mark.asyncio
async def test_query_planner_retries_on_empty_then_succeeds():
    """Empty-then-populated: the step should retry and return a real plan
    once the model produces usable queries on a later attempt.
    """
    planner = QueryPlanner(retry=RetryPolicy(max_attempts=3, initial_delay=0.001))
    original_build_agent = planner.build_agent
    attempts = {"n": 0}

    def build_agent_with_fake_llm():
        attempts["n"] += 1
        agent = original_build_agent()
        payload = (
            json.dumps({"domain_queries": {}})
            if attempts["n"] == 1
            else _domain_queries_payload()
        )
        agent.model = ScriptedLlm(payload=payload)
        return agent

    planner.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]

    plan = await planner.run(
        ResearchRequest(job_id="job1", company="Acme Corp"), NullObserver()
    )

    assert attempts["n"] == 2
    assert plan.company == "Acme Corp"
    assert len(plan.queries) > 0
