"""Tests for QueryPlanner (src/worker/agents/planner.py)."""

from __future__ import annotations

import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import ValidationError

from src.worker.agents.base import AgentError, RetryPolicy
from src.worker.agents.models import ResearchRequest
from src.worker.agents.planner import (
    DOMAIN_DESCRIPTIONS,
    Bm25QuerySelector,
    CandidateQueries,
    DomainName,
    DomainQueryGroup,
    QueryPlanner,
    build_query_generator_prompt,
)
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
            "domain_query_groups": [
                {
                    "domain": "firmographics",
                    "queries": [
                        "Acme Corp revenue 2025",
                        "Acme Corp employee count",
                        "Acme Corp market cap",
                    ],
                },
                {
                    "domain": "tech_stack",
                    "queries": [
                        "Acme Corp cloud infrastructure",
                        "Acme Corp cybersecurity vendor",
                        "Acme Corp network provider",
                    ],
                },
            ]
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
    {"domain_query_groups": []} under output_schema (finish_reason=STOP,
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
        agent.model = ScriptedLlm(payload=json.dumps({"domain_query_groups": []}))
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
            json.dumps({"domain_query_groups": []})
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


def test_domain_name_literal_matches_domain_descriptions():
    """DomainName must cover exactly the same domains as DOMAIN_DESCRIPTIONS
    and Bm25QuerySelector.DOMAIN_LIMITS -- a mismatch would let the schema
    silently accept/reject domains the rest of the pipeline doesn't know
    about (see search.py's DOMAIN_SLUG_TO_OUTPUT_KEY consistency check).
    """
    assert set(DomainName.__args__) == set(DOMAIN_DESCRIPTIONS)
    assert set(DomainName.__args__) == set(Bm25QuerySelector.DOMAIN_LIMITS)


def test_domain_query_group_enforces_typed_domain_and_query_count():
    group = DomainQueryGroup(
        domain="tech_stack",
        queries=[
            "Acme cloud migration 2026",
            "Acme SD-WAN vendor",
            "Acme network provider",
        ],
    )
    assert group.domain == "tech_stack"

    with pytest.raises(ValidationError):
        DomainQueryGroup(domain="not_a_real_domain", queries=["a", "b", "c"])

    with pytest.raises(ValidationError):
        DomainQueryGroup(domain="market", queries=["only one query"])


def test_candidate_queries_schema_is_list_of_typed_groups_not_a_dict():
    """Regression test: the ADK output_schema must be an explicit
    array-of-objects with a closed `domain` enum, not an open-ended
    dict[str, list[str]] -- see DomainQueryGroup's docstring for why the
    dict shape let Gemini return a syntactically valid but empty response.
    """
    schema = CandidateQueries.model_json_schema()
    groups_schema = schema["properties"]["domain_query_groups"]
    assert groups_schema["type"] == "array"


def test_build_query_generator_prompt_includes_domain_descriptions():
    domains = ["firmographics", "tech_stack"]
    prompt = build_query_generator_prompt("Acme Corp", domains, current_year=2026)

    for domain in domains:
        assert domain in prompt
        assert DOMAIN_DESCRIPTIONS[domain] in prompt


def test_build_query_generator_prompt_describes_output_structure_and_sales_goal():
    """response_schema constrains JSON *shape* but does not tell the model
    what to write into each field (see Google's structured-output best
    practices: "Prompt engineering: Clearly state what you want the model
    to do"). The prompt must therefore spell out the expected JSON
    structure explicitly, and frame the objective around the pipeline's
    actual goal -- surfacing a sales opportunity for Colt, not just
    generating generic search queries.
    """
    domains = ["firmographics", "tech_stack"]
    prompt = build_query_generator_prompt("Acme Corp", domains, current_year=2026)

    # Explicit output structure, not just "matching CandidateQueries schema".
    assert "domain_query_groups" in prompt
    assert '"domain"' in prompt
    assert '"queries"' in prompt

    # Sales-lead objective framing.
    assert "sales" in prompt.lower()
    assert "Colt" in prompt
