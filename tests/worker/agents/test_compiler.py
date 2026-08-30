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
from pydantic import Field

from src.worker.agents.base import AgentError, RetryPolicy
from src.worker.agents.compiler import ReportCompiler
from src.worker.agents.models import (
    ColtAlignment,
    ColtAlignmentMapping,
    CompilerInput,
    DomainFinding,
    Evidence,
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
    # When set, overrides `payload` and returns a different draft on each
    # successive call (used to verify the revision loop sees each prior
    # draft's actual content, not a fixed string).
    payloads: list[str] | None = None
    # Captures the exact prompt text sent on each call, in order, so tests
    # can assert what feedback/draft the model actually received on retry.
    seen_prompts: list[str] = Field(default_factory=list)

    async def generate_content_async(self, llm_request, stream: bool = False):
        prompt_text = "".join(
            part.text or ""
            for content in (llm_request.contents or [])
            for part in (content.parts or [])
        )
        self.seen_prompts.append(prompt_text)
        if self.payloads is not None:
            index = min(len(self.seen_prompts) - 1, len(self.payloads) - 1)
            text = self.payloads[index]
        else:
            text = self.payload
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text=text)]),
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


def _make_compiler_with_fake_llm(
    payload: str = "# Strategic Account Brief",
    *,
    payloads: list[str] | None = None,
    max_attempts: int = 2,
) -> tuple[ReportCompiler, ScriptedLlm]:
    """Build a ReportCompiler wired to a ScriptedLlm and return both, so
    tests can inspect ScriptedLlm.seen_prompts after the run.
    """
    compiler = ReportCompiler(
        retry=RetryPolicy(max_attempts=max_attempts, initial_delay=0.001)
    )
    llm = ScriptedLlm(payload=payload, payloads=payloads)
    original_build_agent = compiler.build_agent

    def build_agent_with_fake_llm():
        agent = original_build_agent()
        agent.model = llm
        return agent

    compiler.build_agent = build_agent_with_fake_llm  # type: ignore[method-assign]
    return compiler, llm


def _passing_bm25_result():
    return type("VR", (), {"status": "PASSED", "unsupported": []})()


@pytest.mark.asyncio
async def test_report_compiler_passes_validation():
    compiler, _llm = _make_compiler_with_fake_llm("# Strategic Account Brief")
    compiler._guardrail.validate = AsyncMock(
        return_value=type("R", (), {"is_valid": True, "violations": []})()
    )
    # These tests exercise OutputGuardrail-driven retry orchestration
    # specifically; the BM25 groundedness gate (see
    # test_report_compiler_bm25_gate_* below) is stubbed to PASSED here so
    # it doesn't also need populated SearchFindings.evidence on every
    # fixture just to isolate the guardrail behavior under test.
    compiler._bm25_verifier.verify = lambda *a, **k: _passing_bm25_result()

    report = await compiler.run(_compiler_input(), NullObserver())

    assert report.validation_status == "PASSED"
    assert "Strategic Account Brief" in report.markdown


@pytest.mark.asyncio
async def test_report_compiler_retries_on_failed_validation_then_succeeds():
    compiler, _llm = _make_compiler_with_fake_llm("draft report")
    compiler._bm25_verifier.verify = lambda *a, **k: _passing_bm25_result()
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
    compiler, _llm = _make_compiler_with_fake_llm("draft report")
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


def test_to_input_renders_deduplicated_grounding_evidence_urls():
    """Regression test (found in live testing 2026-08-30): the compiler
    prompt must render the real grounding-citation URLs captured on each
    DomainFinding.evidence as an explicit block, not rely on the model
    re-scanning finding.content prose for incidental inline links.
    """
    findings = SearchFindings(
        company="Acme Corp",
        domains={
            "firmographicsagent_output": DomainFinding(
                domain="firmographics",
                content="Revenue $2B",
                evidence=(
                    Evidence(url="https://a.example.com", query="q1"),
                    Evidence(url="https://b.example.com", query="q1"),
                ),
            ),
            "geographicagent_output": DomainFinding(
                domain="geographic",
                content="HQ London",
                evidence=(
                    Evidence(url="https://b.example.com", query="q2"),  # duplicate
                    Evidence(url="https://c.example.com", query="q2"),
                    Evidence(url="", query="q2"),  # no url: must be skipped
                ),
            ),
        },
        executed=2,
    )
    alignment = ColtAlignment(mappings=(), opportunity_summary="Why now")
    compiler_input = CompilerInput(
        company="Acme Corp", findings=findings, alignment=alignment
    )

    compiler = ReportCompiler()
    prompt = compiler.to_input(compiler_input)

    assert "Verified Source URLs" in prompt
    assert prompt.count("https://a.example.com") == 1
    assert prompt.count("https://b.example.com") == 1  # deduplicated
    assert prompt.count("https://c.example.com") == 1


def test_compiler_retry_attempts_config_defaults_to_three():
    """Regression test: COMPILER_RETRY_ATTEMPTS must be 3 (compile ->
    validate -> fail -> revise -> validate -> fail -> revise -> validate
    -> fail -> give up), matching the user's exact required sequence.
    """
    from src.shared.config import settings

    assert settings.COMPILER_RETRY_ATTEMPTS == 3


@pytest.mark.asyncio
async def test_report_compiler_retries_exactly_three_times_then_fails():
    """Verifies the exact sequence: compile -> validate -> fail -> retry
    with validation error as this same agent's next step -> ... -> after
    the 3rd failure, give up and raise (no 4th attempt).
    """
    compiler, llm = _make_compiler_with_fake_llm("draft report", max_attempts=3)
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

    with pytest.raises(AgentError) as exc_info:
        await compiler.run(_compiler_input(), NullObserver())

    assert exc_info.value.attempts == 3
    assert len(llm.seen_prompts) == 3  # exactly 3 LLM calls, not 4


@pytest.mark.asyncio
async def test_report_compiler_third_attempt_succeeds_after_two_revisions():
    """Full happy-path of the required sequence: 2 failures (each fed back
    as a revision prompt to this same step's next attempt), then success
    on the 3rd attempt -- report is returned, not a 4th attempt.
    """
    compiler, llm = _make_compiler_with_fake_llm(
        payloads=["draft v1 (bad)", "draft v2 (still bad)", "draft v3 (good)"],
        max_attempts=3,
    )
    compiler._bm25_verifier.verify = lambda *a, **k: _passing_bm25_result()
    call_count = {"n": 0}

    async def fake_validate(markdown, raw_search_cache=None):
        call_count["n"] += 1
        if call_count["n"] < 3:
            return type(
                "R",
                (),
                {
                    "is_valid": False,
                    "violations": [
                        type(
                            "V",
                            (),
                            {
                                "rule": "missing_section",
                                "detail": f"Section {call_count['n']} missing",
                            },
                        )()
                    ],
                },
            )()
        return type("R", (), {"is_valid": True, "violations": []})()

    compiler._guardrail.validate = fake_validate

    report = await compiler.run(_compiler_input(), NullObserver())

    assert report.validation_status == "PASSED"
    assert "draft v3 (good)" in report.markdown
    assert call_count["n"] == 3
    assert len(llm.seen_prompts) == 3

    # Attempt 2's prompt must carry attempt 1's specific violation + draft.
    assert "REVISION REQUIRED" in llm.seen_prompts[1]
    assert "Section 1 missing" in llm.seen_prompts[1]
    assert "draft v1 (bad)" in llm.seen_prompts[1]

    # Attempt 3's prompt must carry attempt 2's (not attempt 1's) feedback.
    assert "REVISION REQUIRED" in llm.seen_prompts[2]
    assert "Section 2 missing" in llm.seen_prompts[2]
    assert "draft v2 (still bad)" in llm.seen_prompts[2]
    assert "Section 1 missing" not in llm.seen_prompts[2]

    # Attempt 1's prompt must NOT contain any revision block (nothing to
    # revise yet on the first attempt).
    assert "REVISION REQUIRED" not in llm.seen_prompts[0]


@pytest.mark.asyncio
async def test_report_compiler_revision_state_does_not_leak_across_jobs():
    """Concurrency-safety regression test: two different CompilerInput
    requests processed by the *same* singleton ReportCompiler instance
    must not see each other's revision feedback, since self._revisions is
    keyed by id(request) (see compiler.py::_RevisionState docstring).
    """
    compiler, llm = _make_compiler_with_fake_llm(
        payloads=["job A draft (bad)", "job A draft (good)"], max_attempts=2
    )
    compiler._bm25_verifier.verify = lambda *a, **k: _passing_bm25_result()
    call_count = {"n": 0}

    async def fake_validate(markdown, raw_search_cache=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return type(
                "R",
                (),
                {
                    "is_valid": False,
                    "violations": [
                        type("V", (), {"rule": "x", "detail": "job A issue"})()
                    ],
                },
            )()
        return type("R", (), {"is_valid": True, "violations": []})()

    compiler._guardrail.validate = fake_validate
    await compiler.run(_compiler_input(), NullObserver())

    # A brand-new job's request (fresh CompilerInput object) must start
    # clean -- no leftover "job A issue" revision text in its first prompt.
    llm.payloads = ["job B draft (first attempt)"]
    llm.seen_prompts.clear()
    call_count["n"] = 0

    async def fake_validate_pass(markdown, raw_search_cache=None):
        return type("R", (), {"is_valid": True, "violations": []})()

    compiler._guardrail.validate = fake_validate_pass
    report_b = await compiler.run(_compiler_input(), NullObserver())

    assert report_b.validation_status == "PASSED"
    assert "REVISION REQUIRED" not in llm.seen_prompts[0]
    assert "job A issue" not in llm.seen_prompts[0]


@pytest.mark.asyncio
async def test_report_compiler_bm25_gate_fails_report_with_no_evidence():
    """A structurally-perfect report (OutputGuardrail PASSED) with zero
    search evidence must still be rejected by the BM25 groundedness gate
    and trigger a retry -- proving the gate is real, not a no-op wired in
    alongside OutputGuardrail but never actually able to fail anything.
    """
    compiler, llm = _make_compiler_with_fake_llm(
        "# Strategic Account Brief\nAcme Corp reported $2B revenue in 2025.",
        max_attempts=2,
    )
    compiler._guardrail.validate = AsyncMock(
        return_value=type("R", (), {"is_valid": True, "violations": []})()
    )
    # _compiler_input() findings have no Evidence at all (see helper
    # above) -- Bm25Verifier.verify() returns FAILED immediately with "No
    # search evidence found" when EvidenceStore.documents() is empty.
    with pytest.raises(AgentError) as exc_info:
        await compiler.run(_compiler_input(), NullObserver())

    assert "bm25_groundedness" in str(exc_info.value)
    assert len(llm.seen_prompts) == 2  # actually retried, not skipped


@pytest.mark.asyncio
async def test_report_compiler_bm25_gate_passes_report_grounded_in_evidence():
    """A report whose claims are actually supported by real search
    evidence must pass the BM25 gate (no false-positive rejection).
    """
    findings = SearchFindings(
        company="Acme Corp",
        domains={
            "firmographicsagent_output": DomainFinding(
                domain="firmographics",
                content="Acme Corp reported two billion dollars in revenue for fiscal year 2025, a significant milestone for the company.",
                evidence=(
                    Evidence(
                        url="https://acme.example.com/investors",
                        title="Acme Investor Relations",
                        snippet=(
                            "Acme Corp reported two billion dollars in "
                            "revenue for fiscal year 2025, a significant "
                            "milestone for the company and its shareholders "
                            "worldwide across all major markets."
                        ),
                        query="Acme Corp revenue 2025",
                    ),
                ),
            ),
        },
        executed=1,
    )
    alignment = ColtAlignment(
        mappings=(ColtAlignmentMapping("legacy WAN", "SD-WAN", "modernize"),),
        opportunity_summary="Why Colt, why now",
    )
    compiler_input = CompilerInput(
        company="Acme Corp", findings=findings, alignment=alignment
    )

    # Bm25Verifier's leniency rule (see verification.py::Bm25Verifier.verify)
    # requires at least 5 checkable claims before it will tolerate any
    # unsupported ones; a short one-sentence draft doesn't reach that
    # floor, so this uses a handful of sentences all grounded in the same
    # evidence, matching how real ~40K-char compiled reports behave.
    grounded_report = (
        "Acme Corp reported two billion dollars in revenue for fiscal year "
        "2025, a significant milestone for the company. "
        "The company's revenue growth in fiscal year 2025 reached two "
        "billion dollars overall. "
        "Acme Corp's fiscal year 2025 results showed two billion dollars "
        "in total revenue for the business. "
        "This two billion dollar revenue figure for fiscal year 2025 "
        "represents strong performance for Acme Corp. "
        "Analysts noted that Acme Corp's two billion dollar revenue in "
        "fiscal year 2025 exceeded expectations for the company."
    )
    compiler, _llm = _make_compiler_with_fake_llm(grounded_report)
    compiler._guardrail.validate = AsyncMock(
        return_value=type("R", (), {"is_valid": True, "violations": []})()
    )

    report = await compiler.run(compiler_input, NullObserver())

    assert report.validation_status == "PASSED"


@pytest.mark.asyncio
async def test_report_compiler_bm25_gate_can_be_disabled_via_settings(monkeypatch):
    """REPORT_COMPILER_BM25_GATE_ENABLED=False must fully bypass the BM25
    check, so a report with zero evidence (which would otherwise always
    fail the gate) can still pass on OutputGuardrail success alone.
    """
    from src.worker.agents import compiler as compiler_module

    monkeypatch.setattr(
        compiler_module.settings, "REPORT_COMPILER_BM25_GATE_ENABLED", False
    )
    compiler, _llm = _make_compiler_with_fake_llm("# Strategic Account Brief")
    compiler._guardrail.validate = AsyncMock(
        return_value=type("R", (), {"is_valid": True, "violations": []})()
    )

    report = await compiler.run(_compiler_input(), NullObserver())

    assert report.validation_status == "PASSED"


def test_to_input_handles_no_grounding_evidence():
    findings = SearchFindings(
        company="Acme Corp",
        domains={
            "firmographicsagent_output": DomainFinding(
                domain="firmographics", content="Revenue $2B"
            ),
        },
        executed=1,
    )
    alignment = ColtAlignment(mappings=(), opportunity_summary="Why now")
    compiler_input = CompilerInput(
        company="Acme Corp", findings=findings, alignment=alignment
    )

    compiler = ReportCompiler()
    prompt = compiler.to_input(compiler_input)

    assert "(no grounding citation URLs available)" in prompt
