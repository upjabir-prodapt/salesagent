"""Tests for PlanReAct FINAL_ANSWER → output_key persistence helpers."""

import importlib.util
from pathlib import Path

from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG

_ROOT = Path(__file__).resolve().parents[2]
_MOD = _ROOT / "src/services/research/agent/sales/utils/output_persistence.py"
_spec = importlib.util.spec_from_file_location("output_persistence", _MOD)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

extract_final_answer_payload = _mod.extract_final_answer_payload
persist_output_key = _mod.persist_output_key
persist_output_from_session_events = _mod.persist_output_from_session_events


def test_extract_final_answer_payload_after_tag():
    text = f"/*PLANNING*/ step {FINAL_ANSWER_TAG}\n{{\"company\": \"Acme\"}}"
    out = extract_final_answer_payload(text)
    assert out is not None
    assert "Acme" in out


def test_persist_output_key_writes_state():
    state: dict = {}
    ok = persist_output_key(
        state,
        agent_name="GrowthSignals",
        output_key="growthsignals_output",
        text=f"{FINAL_ANSWER_TAG}\n{{\"signals\": []}}",
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
            f"{FINAL_ANSWER_TAG}\n{{\"growth\": true}}",
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
