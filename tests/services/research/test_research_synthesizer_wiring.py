"""Wiring tests for the ResearchSynthesizer domain-output persistence layers."""

from __future__ import annotations

import json
from types import SimpleNamespace

from src.services.research.agents.sales.composition import research_synthesizer as rs
from src.services.research.domain.agent_contracts import DOMAIN_OUTPUT_KEYS

PAYLOAD = {"summary": "a" * 80}


def _blob(keys) -> str:
    return "/*FINAL_ANSWER*/\n" + json.dumps(dict.fromkeys(keys, PAYLOAD))


def _response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(text=text, thought=False)])
    )


def _event(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        author=rs.SYNTHESIZER_NAME,
        invocation_id="inv-1",
        content=SimpleNamespace(parts=[SimpleNamespace(text=text, thought=False)]),
    )


def _context(state: dict, events: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        invocation_id="inv-1",
        session=SimpleNamespace(events=events or []),
    )


def test_agent_exposes_the_save_tool_and_keeps_persistence_first() -> None:
    agent = rs.create_research_synthesizer("Sephora")

    tool_names = {getattr(tool, "name", type(tool).__name__) for tool in agent.tools}
    assert "save_domain_output" in tool_names

    assert agent.before_model_callback[0] is rs._inject_domain_progress_before_model
    assert agent.after_model_callback[0] is rs._persist_domain_outputs_after_model
    assert agent.after_agent_callback[0] is rs._recover_domain_outputs_after_agent


def test_after_model_recovers_domains_from_a_json_final_answer() -> None:
    state: dict = {}

    rs._persist_domain_outputs_after_model(
        _context(state), _response(_blob(DOMAIN_OUTPUT_KEYS))
    )

    assert set(state) == set(DOMAIN_OUTPUT_KEYS)


def test_after_agent_sweeps_events_for_domains_the_model_never_finalized() -> None:
    state: dict = {}
    events = [_event("/*AGGREGATED_ANSWER*/"), _event(_blob(DOMAIN_OUTPUT_KEYS[:8]))]

    rs._recover_domain_outputs_after_agent(_context(state, events))

    assert set(state) == set(DOMAIN_OUTPUT_KEYS[:8])


def test_after_agent_falls_back_to_the_stored_agent_output() -> None:
    state = {"research_synthesizer_output": _blob(DOMAIN_OUTPUT_KEYS[:3])}

    rs._recover_domain_outputs_after_agent(_context(state))

    assert set(DOMAIN_OUTPUT_KEYS[:3]).issubset(state)


def test_progress_hint_lists_only_the_missing_domains() -> None:
    state = dict.fromkeys(DOMAIN_OUTPUT_KEYS[:9], "saved")
    appended: list[str] = []
    request = SimpleNamespace(append_instructions=appended.extend)

    rs._inject_domain_progress_before_model(_context(state), request)

    assert len(appended) == 1
    for key in DOMAIN_OUTPUT_KEYS[9:]:
        assert key in appended[0]
    assert DOMAIN_OUTPUT_KEYS[0] not in appended[0]


def test_progress_hint_silent_when_nothing_or_everything_is_saved() -> None:
    appended: list[str] = []
    request = SimpleNamespace(append_instructions=appended.extend)

    rs._inject_domain_progress_before_model(_context({}), request)
    rs._inject_domain_progress_before_model(
        _context(dict.fromkeys(DOMAIN_OUTPUT_KEYS, "saved")), request
    )

    assert appended == []
