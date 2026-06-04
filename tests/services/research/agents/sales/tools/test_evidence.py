from __future__ import annotations

import json

from src.services.research.agents.sales.tools import evidence


def test_normalize_entry_maps_uri_and_flags() -> None:
    entry = evidence.normalize_entry(
        {
            "uri": "https://www.reuters.com/article/1",
            "title": "Headline",
            "body": "Snippet text",
            "flagged_injection": True,
        },
        agent_name="MarketAgent",
    )

    assert entry["url"] == "https://www.reuters.com/article/1"
    assert entry["agent"] == "MarketAgent"
    assert entry["authoritative"] is True
    assert entry["flagged_injection"] is True


def test_append_and_get_agent_evidence() -> None:
    state: dict = {}
    length = evidence.append_evidence(
        state,
        "ExecutiveAgent",
        [{"url": "https://bbc.com/news", "title": "News"}],
    )

    assert length == 1
    assert len(evidence.get_agent_evidence(state, "ExecutiveAgent")) == 1
    assert evidence.append_evidence(state, "", [{"url": "x"}]) == 0
    assert evidence.append_evidence(state, "ExecutiveAgent", []) == 1


def test_aggregate_job_evidence_dedupes_and_merges_legacy() -> None:
    state = {
        "search_evidence_MarketAgent": [
            {"url": "https://reuters.com/a", "title": "A"},
            {"url": "https://reuters.com/a", "title": "dup"},
        ],
        "search_evidence": [{"url": "https://ft.com/b", "title": "B"}],
        "raw_search_cache_LegacyAgent": [
            {"uri": "https://wsj.com/c", "title": "C", "agent": "LegacyAgent"},
        ],
        "raw_search_cache": [{"link": "https://bbc.com/d", "title": "D"}],
    }

    merged = evidence.aggregate_job_evidence(state)

    urls = {e["url"] for e in merged}
    assert "https://reuters.com/a" in urls
    assert "https://ft.com/b" in urls
    assert "https://wsj.com/c" in urls
    assert "https://bbc.com/d" in urls
    assert len(merged) == 4


def test_evidence_text_and_block_truncation() -> None:
    entries = [
        {"title": "T1", "snippet": "short", "agent": "A", "url": "https://a.com"},
        {"title": "T2", "snippet": "text", "agent": "B", "url": "https://b.com"},
    ]

    text = evidence.evidence_to_text(entries, max_chars=30)
    assert "T1" in text
    assert "Agent: A" in evidence.evidence_to_block(entries, max_chars=80)


def test_format_agent_outputs_for_judge() -> None:
    state = {
        "strategyagent_output": json.dumps({"summary": "ok"}),
        "marketagent_output": "plain text",
        "final_report": "ignored",
        "StrategyAgent_bm25_status": "ok",
        evidence.verification_status_key("StrategyAgent"): "PASSED",
        "StrategyAgent_verification_result": "all good",
    }
    keys = {
        "StrategyAgent": "strategyagent_output",
        "MarketAgent": "marketagent_output",
        "ReportCompiler": "final_report",
    }

    block = evidence.format_agent_outputs_for_judge(
        state, keys, max_chars_per_agent=100
    )

    assert "StrategyAgent" in block
    assert "verification_status=PASSED" in block
    assert "MarketAgent" in block


def test_verification_state_helpers() -> None:
    state: dict = {}
    evidence.set_verification_state(
        state, "RiskSignals", status="FAILED", unsupported=["claim-1"]
    )

    assert evidence.get_verification_status(state, "RiskSignals") == "FAILED"
    assert evidence.get_unsupported_claims(state, "RiskSignals") == ["claim-1"]
    assert evidence.verification_status_key("RiskSignals").endswith("_status")
