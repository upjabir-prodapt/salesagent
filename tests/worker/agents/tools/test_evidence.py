"""Tests for src/worker/agents/tools/evidence.py.

Restores coverage lost when test_research_characterization.py (which mixed
evidence tests with tests of the deleted callback module) was removed
during the agent pipeline cutover.
"""

from __future__ import annotations

from src.worker.agents.tools.evidence import (
    aggregate_job_evidence,
    append_evidence,
    evidence_key,
    evidence_to_block,
    evidence_to_text,
    format_agent_outputs_for_judge,
    get_agent_evidence,
    get_unsupported_claims,
    get_verification_status,
    normalize_entry,
    set_verification_state,
    verification_status_key,
    verification_unsupported_key,
)


def test_evidence_key_naming():
    assert evidence_key("ExecutiveAgent") == "search_evidence_ExecutiveAgent"
    assert verification_status_key("A") == "verification_A_status"
    assert verification_unsupported_key("A") == "verification_A_unsupported_claims"


def test_normalize_entry_maps_uri_and_link_to_url():
    entry = normalize_entry({"uri": "https://reuters.com/a", "title": "T"})
    assert entry["url"] == "https://reuters.com/a"
    assert entry["authoritative"] is True

    entry2 = normalize_entry({"link": "https://unknown.example/x"})
    assert entry2["url"] == "https://unknown.example/x"
    assert entry2["authoritative"] is False


def test_normalize_entry_flags_injection():
    entry = normalize_entry({"url": "https://x.com", "flagged_injection": True})
    assert entry["flagged_injection"] is True


def test_normalize_entry_truncates_long_fields():
    long_title = "x" * 500
    long_snippet = "y" * 1000
    entry = normalize_entry({"title": long_title, "snippet": long_snippet})
    assert len(entry["title"]) == 200
    assert len(entry["snippet"]) == 600


def test_append_evidence_and_get_agent_evidence_roundtrip():
    state: dict = {}
    n = append_evidence(
        state,
        "ExecutiveAgent",
        [{"url": "https://example.com/a", "title": "A"}],
    )
    assert n == 1
    assert len(get_agent_evidence(state, "ExecutiveAgent")) == 1
    assert get_agent_evidence(state, "OtherAgent") == []


def test_append_evidence_noop_on_empty_inputs():
    state: dict = {}
    assert append_evidence(state, "", [{"url": "x"}]) == 0
    assert append_evidence(state, "Agent", []) == 0
    assert state == {}


def test_aggregate_job_evidence_merges_and_dedupes_by_url():
    state = {
        "search_evidence_ExecutiveAgent": [
            {"url": "https://example.com/leadership", "title": "Leadership"},
        ],
        "search_evidence_StrategyAgent": [
            {"uri": "https://example.com/leadership", "title": "Duplicate"},
            {"url": "https://example.com/strategy", "title": "Strategy"},
        ],
    }
    merged = aggregate_job_evidence(state)
    urls = {e["url"] for e in merged}
    assert urls == {"https://example.com/leadership", "https://example.com/strategy"}
    assert len(merged) == 2


def test_aggregate_job_evidence_includes_legacy_raw_search_cache():
    state = {
        "raw_search_cache": [
            {"link": "https://alt.example.com/news", "description": "Expansion"}
        ]
    }
    merged = aggregate_job_evidence(state)
    assert merged[0]["url"] == "https://alt.example.com/news"
    assert merged[0]["snippet"] == "Expansion"


def test_aggregate_job_evidence_dedupes_entries_without_url_by_title_snippet():
    state = {
        "search_evidence_A": [
            {"title": "T1", "snippet": "S1"},
            {"title": "T1", "snippet": "S1"},
        ]
    }
    merged = aggregate_job_evidence(state)
    assert len(merged) == 1


def test_evidence_to_text_joins_title_and_snippet():
    entries = [{"title": "T1", "snippet": "S1"}, {"title": "", "snippet": "S2"}]
    text = evidence_to_text(entries)
    assert "T1" in text
    assert "S1" in text
    assert "S2" in text


def test_evidence_to_text_respects_max_chars():
    entries = [{"title": "T", "snippet": "x" * 100} for _ in range(5)]
    text = evidence_to_text(entries, max_chars=50)
    assert len(text) < 500


def test_evidence_to_block_formats_agent_and_url():
    entries = [{"agent": "A", "title": "T", "url": "https://x.com", "snippet": "S"}]
    block = evidence_to_block(entries)
    assert "[Agent: A] T" in block
    assert "URL: https://x.com" in block
    assert "S" in block


def test_evidence_to_block_stops_at_max_chars():
    entries = [
        {"agent": "A", "title": "T", "url": "https://x.com", "snippet": "y" * 200}
        for _ in range(10)
    ]
    block = evidence_to_block(entries, max_chars=100)
    assert len(block) < 1000


def test_format_agent_outputs_for_judge_skips_final_report_and_empty():
    state = {
        "final_report": "should be skipped",
        "executiveagent_output": "CEO is Jane Doe",
        "emptyagent_output": "",
    }
    keys = {
        "ReportCompiler": "final_report",
        "ExecutiveAgent": "executiveagent_output",
        "EmptyAgent": "emptyagent_output",
    }
    block = format_agent_outputs_for_judge(state, keys)
    assert "should be skipped" not in block
    assert "ExecutiveAgent" in block
    assert "CEO is Jane Doe" in block
    assert "EmptyAgent" not in block


def test_format_agent_outputs_for_judge_pretty_prints_json_strings():
    state = {"a_output": '{"key": "value"}'}
    block = format_agent_outputs_for_judge(state, {"A": "a_output"})
    assert '"key": "value"' in block


def test_format_agent_outputs_for_judge_truncates_long_bodies():
    state = {"a_output": "x" * 5000}
    block = format_agent_outputs_for_judge(
        state, {"A": "a_output"}, max_chars_per_agent=100
    )
    assert "[truncated]" in block


def test_set_and_get_verification_state_scoped_and_legacy():
    state: dict = {}
    set_verification_state(state, "ExecutiveAgent", status="PASSED", unsupported=["c1"])

    assert get_verification_status(state, "ExecutiveAgent") == "PASSED"
    assert get_unsupported_claims(state, "ExecutiveAgent") == ["c1"]
    # legacy global fallback still populated
    assert state["verification_status"] == "PASSED"


def test_get_verification_status_falls_back_to_legacy_global():
    state = {"verification_status": "FAILED"}
    assert get_verification_status(state, "UnknownAgent") == "FAILED"


def test_get_unsupported_claims_returns_empty_list_when_absent():
    assert get_unsupported_claims({}, "Agent") == []
