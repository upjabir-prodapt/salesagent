"""Tests for PlanReAct FINAL_ANSWER -> output_key persistence helpers."""

from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG

from src.worker.agents.tools.output_persistence import (
    extract_final_answer_payload,
    persist_output_from_session_events,
    persist_output_key,
)


def test_extract_final_answer_payload_after_tag():
    text = f'/*PLANNING*/ step {FINAL_ANSWER_TAG}\n{{"company": "Acme"}}'
    out = extract_final_answer_payload(text)
    assert out is not None
    assert "Acme" in out


def test_persist_output_key_writes_state():
    state: dict = {}
    ok = persist_output_key(
        state,
        agent_name="GrowthSignals",
        output_key="growthsignals_output",
        text=f'{FINAL_ANSWER_TAG}\n{{"signals": []}}',
    )
    assert ok is True
    assert state["growthsignals_output"]


def test_persist_output_from_events():
    class _Part:
        def __init__(self, text: str):
            self.text = text
            self.thought = False

    class _Content:
        def __init__(self, text: str):
            self.parts = [_Part(text)]

    class _Event:
        def __init__(self, author: str, text: str, inv: str):
            self.author = author
            self.invocation_id = inv
            self.content = _Content(text)

    state: dict = {}
    events = [
        _Event(
            "GrowthSignals",
            f'{FINAL_ANSWER_TAG}\n{{"growth": true}}',
            "inv-1",
        )
    ]
    ok = persist_output_from_session_events(
        state,
        events,
        agent_name="GrowthSignals",
        output_key="growthsignals_output",
        invocation_id="inv-1",
    )
    assert ok is True
    assert "growth" in state["growthsignals_output"]


def test_persist_output_key_strips_control_tags_without_final_answer():
    state: dict = {}
    text = (
        "/*PLANNING*/ checklist\n"
        "/*ACTION*/ call validate_final_report\n"
        "/*AGGREGATED_ANSWER*/\n"
        "## Company Snapshot\n- Company Name: Acme"
    )
    ok = persist_output_key(
        state,
        agent_name="ReportCompiler",
        output_key="final_report",
        text=text,
    )
    assert ok is True
    assert "/*PLANNING*/" not in state["final_report"]
    assert "/*AGGREGATED_ANSWER*/" not in state["final_report"]
    assert "## Company Snapshot" in state["final_report"]
    assert "checklist" not in state["final_report"]


def test_persist_output_key_prefers_final_answer_payload_and_strips_tags():
    state: dict = {}
    text = (
        "/*PLANNING*/ notes\n"
        f"{FINAL_ANSWER_TAG}\n"
        "## Company Overview\n- Revenue: 100\n/*REPLANNING*/"
    )
    ok = persist_output_key(
        state,
        agent_name="ReportCompiler",
        output_key="final_report",
        text=text,
    )
    assert ok is True
    assert "/*FINAL_ANSWER*/" not in state["final_report"]
    assert "/*REPLANNING*/" not in state["final_report"]
    assert "## Company Overview" in state["final_report"]
