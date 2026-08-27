"""Tests for ReportCompiler (src/worker/agents/compiler.py).

Verifies: CompilerInput is exactly findings + alignment (no other input,
per the user's design requirement), and that a FAILED guardrail result
triggers a step-level retry via InvalidOutputError (replacing the old
PlanReAct-tag-based validate_final_report tool loop).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from src.worker.agents.base import AgentError, RetryPolicy
from src.worker.agents.compiler import ReportCompiler
from src.worker.agents.models import (
    ColtAlignment,
    ColtAlignmentMapping,
    CompilerInput,
    DomainFinding,
    SearchFindings,
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
    model: str = "fake-compiler"
    payload: str = "# Strategic Account Brief"

    async def generate_content_async(self, llm_request, stream: bool = False):
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=self.payload)]),
            usage_metadata=types.GenerateContentResponseUsageMetadata(
                prompt_token_count=30, candidates_token_count=200
            ),
        )


def _compiler_input() -> CompilerInput:
    findings = SearchFindings(
        company="Acme Corp",
        domains={
            "firmographicsagent_output": DomainFinding(
                domain="firmographics", content="Revenue $2B"
            ),
        },
        executed=1,
    )
    alignment = ColtAlignment(
        mappings=(ColtAlignmentMapping("legacy WAN", "SD-WAN", "modernize"),),
        opportunity_summary="Why Colt, why now",
    )
    return CompilerInput(company="Acme Corp", findings=findings, alignment=alignment)


def _make_compiler_with_fake_llm(payload: str) -> ReportCompiler:
    compiler = ReportCompiler(retry=RetryPolicy(max_attempts=2, initial_delay=0.001))
    original_build_agent = compiler.build_agent

    def build_agent_with_fake_llm():
        agent = original_build_agent()
        agent.model = ScriptedLlm(payload=payload)
        return agent

    compiler.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]
    return compiler


@pytest.mark.asyncio
async def test_report_compiler_passes_validation():
    compiler = _make_compiler_with_fake_llm("# Strategic Account Brief")
    compiler._guardrail.validate = AsyncMock(
        return_value=type("R", (), {"is_valid": True, "violations": []})()
    )

    report = await compiler.run(_compiler_input(), NullObserver())

    assert report.validation_status == "PASSED"
    assert "Strategic Account Brief" in report.markdown


@pytest.mark.asyncio
async def test_report_compiler_retries_on_failed_validation_then_succeeds():
    compiler = _make_compiler_with_fake_llm("draft report")
    call_count = {"n": 0}

    async def fake_validate(markdown, raw_search_cache=None):
        call_count["n"] += 1
        is_valid = call_count["n"] >= 2
        violations = [] if is_valid else [type("V", (), {"rule": "x", "detail": "y"})()]
        return type("R", (), {"is_valid": is_valid, "violations": violations})()

    compiler._guardrail.validate = fake_validate

    report = await compiler.run(_compiler_input(), NullObserver())

    assert report.validation_status == "PASSED"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_report_compiler_exhausts_retries_on_persistent_validation_failure():
    compiler = _make_compiler_with_fake_llm("draft report")
    compiler._guardrail.validate = AsyncMock(
        return_value=type(
            "R",
            (),
            {
                "is_valid": False,
                "violations": [type("V", (), {"rule": "x", "detail": "y"})()],
            },
        )()
    )

    with pytest.raises(AgentError):
        await compiler.run(_compiler_input(), NullObserver())


def test_compiler_input_carries_only_findings_and_alignment():
    """Regression test: CompilerInput must not carry a query plan or raw
    session context -- only company + findings + alignment.
    """
    ci = _compiler_input()
    fields = set(ci.__dataclass_fields__.keys())
    assert fields == {"company", "findings", "alignment"}
