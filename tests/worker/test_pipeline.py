"""End-to-end test for ResearchPipeline across all 4 steps with fake LLMs.

Verifies the full data flow the user specified: QueryPlanner -> (typed
QueryPlan) -> SearchExecutor -> (typed SearchFindings) -> AlignmentAnalyst
-> (typed ColtAlignment) -> ReportCompiler(findings + alignment only).
No step receives another step's raw session/context.
"""

from __future__ import annotations

import json

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types as genai_types

from src.worker.agents.alignment import AlignmentAnalyst
from src.worker.agents.compiler import ReportCompiler
from src.worker.agents.models import ResearchRequest
from src.worker.agents.planner import QueryPlanner
from src.worker.agents.search import SearchExecutor
from src.worker.observers import Observer
from src.worker.pipeline import ResearchPipeline


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
    model: str = "fake-e2e"
    payload: str = "{}"

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(
            content=genai_types.Content(
                role="model", parts=[genai_types.Part(text=self.payload)]
            ),
            usage_metadata=genai_types.GenerateContentResponseUsageMetadata(
                prompt_token_count=10, candidates_token_count=20
            ),
        )


def _patch_llm(step, payload: str) -> None:
    original_build_agent = step.build_agent

    def build_agent_with_fake_llm():
        agent = original_build_agent()
        agent.model = ScriptedLlm(payload=payload)
        return agent

    step.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]


class FakeCache:
    async def async_get_search(self, company, query):
        return None

    async def async_set_search(self, company, query, results, domain=None):
        return True


class FakeSearchModels:
    async def generate_content(self, *, model, contents, config):
        from types import SimpleNamespace

        return SimpleNamespace(text="Acme facts found via search", candidates=[])


class FakeSearchAsync:
    def __init__(self):
        self.models = FakeSearchModels()


class FakeSearchClient:
    def __init__(self):
        self.aio = FakeSearchAsync()


@pytest.mark.asyncio
async def test_full_pipeline_produces_report_and_legacy_state(monkeypatch):
    # 1. QueryPlanner
    planner = QueryPlanner()
    _patch_llm(
        planner,
        json.dumps(
            {
                "domain_query_groups": [
                    {
                        "domain": "firmographics",
                        "queries": [
                            "Acme Corp revenue 2025",
                            "Acme Corp employee count",
                            "Acme Corp ownership structure",
                        ],
                    },
                    {
                        "domain": "tech_stack",
                        "queries": [
                            "Acme Corp cloud stack",
                            "Acme Corp network vendor",
                            "Acme Corp SD-WAN provider",
                        ],
                    },
                ]
            }
        ),
    )

    # 2. SearchExecutor with a fake genai client
    searcher = SearchExecutor(
        FakeSearchClient(),
        FakeCache(),
        model="fake-search-model",
        qps=1000.0,
        qps_burst=1000,
        concurrency=8,
    )

    # 3. AlignmentAnalyst
    analyst = AlignmentAnalyst()
    _patch_llm(
        analyst,
        json.dumps(
            {
                "alignment_mappings": [
                    {
                        "challenge_or_priority": "Legacy WAN",
                        "colt_solution": "Colt SD-WAN",
                        "alignment_justification": "Modernizes connectivity",
                    }
                ],
                "strategic_opportunity": {
                    "summary": "Why Colt, why now",
                    "hooks": ["Digital transformation"],
                },
            }
        ),
    )

    # 4. ReportCompiler
    compiler = ReportCompiler()
    _patch_llm(compiler, "# Strategic Account Brief\n\nFull report body here.")

    from unittest.mock import AsyncMock

    compiler._guardrail.validate = AsyncMock(
        return_value=type("R", (), {"is_valid": True, "violations": []})()
    )

    pipeline = ResearchPipeline(planner, searcher, analyst, compiler)
    request = ResearchRequest(job_id="job-e2e-1", company="Acme Corp")

    result = await pipeline.run(request, NullObserver())

    assert result.report.validation_status == "PASSED"
    assert "Strategic Account Brief" in result.report.markdown
    assert result.findings.company == "Acme Corp"
    assert result.findings.executed == 6  # all 6 queries (2 domains x 3) succeeded
    assert len(result.alignment.mappings) == 1

    state = result.to_legacy_state()
    expected_keys = {
        "company_name",
        "job_evidence",
        "raw_search_cache",
        "agent_telemetry_records",
        "mc_input_tokens",
        "mc_output_tokens",
        "mc_tokens_by_model",
        "mc_temperature",
        "mc_search_count",
        "report_validation_status",
        "report_validation_violations",
    }
    assert expected_keys.issubset(state.keys())
    assert state["company_name"] == "Acme Corp"
    assert state["report_validation_status"] == "PASSED"
    assert state["mc_search_count"] == 6
    # TelemetryObserver records one row per step attempt (all 4 steps),
    # but only the 3 AdkAgentStep-backed steps (planner, alignment,
    # compiler) report LLM token usage via on_usage(); SearchExecutor
    # bypasses ADK entirely and reports no token usage through this path.
    assert len(result.telemetry_records) == 4
    assert state["mc_input_tokens"] > 0
    assert state["mc_output_tokens"] > 0
    assert len(result.token_usage_by_model) == 1  # all 3 LLM steps share "fake-e2e"
