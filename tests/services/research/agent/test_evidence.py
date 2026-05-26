"""Tests for evidence registry."""

from src.services.research.agent.sales.utils.evidence import (
    aggregate_job_evidence,
    append_evidence,
    evidence_key,
    get_agent_evidence,
    normalize_entry,
)


def test_normalize_entry_maps_uri_to_url():
    entry = normalize_entry(
        {"uri": "https://example.com", "title": "T", "snippet": "S"},
        agent_name="FirmographicsAgent",
    )
    assert entry["url"] == "https://example.com"
    assert entry["agent"] == "FirmographicsAgent"


def test_per_agent_evidence_keys():
    state: dict = {}
    append_evidence(
        state,
        "FirmographicsAgent",
        [{"url": "https://a.com", "title": "A", "snippet": "sa"}],
    )
    append_evidence(
        state,
        "StrategyAgent",
        [{"url": "https://b.com", "title": "B", "snippet": "sb"}],
    )
    assert len(get_agent_evidence(state, "FirmographicsAgent")) == 1
    assert len(get_agent_evidence(state, "StrategyAgent")) == 1
    assert evidence_key("FirmographicsAgent") in state


def test_aggregate_job_evidence_dedupes_and_legacy():
    state = {
        evidence_key("FirmographicsAgent"): [
            {"url": "https://a.com", "snippet": "x"},
        ],
        "raw_search_cache_FirmographicsAgent_abc": [
            {"uri": "https://a.com", "snippet": "x"},
        ],
        "raw_search_cache_other": [
            {"url": "https://b.com", "snippet": "y"},
        ],
    }
    merged = aggregate_job_evidence(state)
    urls = {e["url"] for e in merged}
    assert "https://a.com" in urls
    assert "https://b.com" in urls
    assert len(merged) == 2
