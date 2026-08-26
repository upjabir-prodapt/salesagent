"""Tests for the typed IO dataclasses and the PipelineResult legacy bridge.

The bridge (to_legacy_state) must emit exactly the keys that
finalization_service, evaluation/, artifacts.py, and metrics.py read from
session_state today -- verified in IMPLEMENTATION_PLAN.md section 10.1.
"""

from __future__ import annotations

from src.worker.agents.models import (
    ColtAlignment,
    ColtAlignmentMapping,
    DomainFinding,
    Evidence,
    PipelineResult,
    Query,
    QueryPlan,
    QueryResult,
    Report,
    SearchFindings,
)

_LEGACY_KEYS = {
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


def _make_findings() -> SearchFindings:
    evidence = (Evidence(url="https://reuters.com/a", title="A", snippet="s"),)
    return SearchFindings(
        company="Acme",
        domains={
            "firmographicsagent_output": DomainFinding(
                domain="firmographics", content="Revenue $1B", evidence=evidence
            ),
        },
        executed=5,
        failed=("q1",),
    )


def _make_alignment() -> ColtAlignment:
    return ColtAlignment(
        mappings=(ColtAlignmentMapping("challenge", "solution", "why"),),
        opportunity_summary="Why Colt, why now",
    )


def test_query_result_ok_and_failed_factories():
    q = Query(text="Acme revenue 2025", domain="firmographics")
    ok = QueryResult.ok(q, "some text", ())
    assert ok.succeeded is True
    assert ok.error_kind is None

    failed = QueryResult.failed(q, "RATE_LIMIT")
    assert failed.succeeded is False
    assert failed.text == ""
    assert failed.error_kind == "RATE_LIMIT"


def test_search_findings_success_rate():
    findings = _make_findings()
    assert findings.total_queries == 6
    assert round(findings.success_rate, 4) == round(5 / 6, 4)


def test_search_findings_success_rate_zero_queries_is_one():
    findings = SearchFindings(company="Acme", domains={}, executed=0, failed=())
    assert findings.success_rate == 1.0


def test_search_findings_all_evidence_flattens_domains():
    findings = _make_findings()
    assert len(findings.all_evidence()) == 1
    assert findings.all_evidence()[0].url == "https://reuters.com/a"


def test_query_plan_is_immutable():
    plan = QueryPlan(company="Acme", queries=(Query("q", "d"),))
    try:
        plan.company = "Other"  # type: ignore[misc]
        raised = False
    except AttributeError:
        raised = True
    assert raised


def test_pipeline_result_to_legacy_state_has_exact_key_set():
    result = PipelineResult(
        report=Report(markdown="# Report", validation_status="PASSED"),
        findings=_make_findings(),
        alignment=_make_alignment(),
        telemetry_records=[{"agent_name": "QueryPlanner"}],
        token_usage_by_model={"gemini-3.5-flash": {"input": 100, "output": 50}},
        temperature=0.0,
    )
    state = result.to_legacy_state()
    assert _LEGACY_KEYS.issubset(state.keys())
    assert state["company_name"] == "Acme"
    assert state["mc_input_tokens"] == 100
    assert state["mc_output_tokens"] == 50
    assert state["mc_search_count"] == 5
    assert state["search_count"] == 5
    assert state["report_validation_status"] == "PASSED"
    assert len(state["job_evidence"]) == 1
    assert state["raw_search_cache"] == state["job_evidence"]


def test_pipeline_result_to_legacy_state_empty_usage_defaults_to_zero():
    result = PipelineResult(
        report=Report(markdown="# Report"),
        findings=SearchFindings(company="Acme", domains={}, executed=0),
        alignment=_make_alignment(),
    )
    state = result.to_legacy_state()
    assert state["mc_input_tokens"] == 0
    assert state["mc_output_tokens"] == 0
    assert state["mc_tokens_by_model"] == {}
