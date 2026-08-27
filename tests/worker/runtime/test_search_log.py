"""Tests for src/worker/runtime/search_log.py."""

from __future__ import annotations

from src.worker.runtime.search_log import (
    SEARCH_COUNT_KEY,
    SEARCH_QUERY_RECORDS_KEY,
    get_search_count,
    get_search_query_records,
    query_hash,
    record_search_query,
)


def test_query_hash_is_stable_and_case_insensitive():
    a = query_hash("Acme Corp revenue")
    b = query_hash("acme corp revenue")
    assert a == b
    assert len(a) == 16


def test_record_search_query_appends_and_bumps_counter():
    state: dict = {}
    record_search_query(state, query="Acme revenue", agent_name="SearchExecutor")
    record_search_query(state, query="Acme leadership", agent_name="SearchExecutor")

    assert state[SEARCH_COUNT_KEY] == 2
    assert len(state[SEARCH_QUERY_RECORDS_KEY]) == 2
    assert state[SEARCH_QUERY_RECORDS_KEY][0]["query"] == "Acme revenue"


def test_record_search_query_ignores_blank_query():
    state: dict = {}
    record_search_query(state, query="   ", agent_name="A")
    assert state == {}


def test_record_search_query_noop_on_non_mutable_state():
    # state without __setitem__ (e.g. None) must not raise.
    record_search_query(None, query="q", agent_name="A")


def test_record_search_query_uses_agent_name_as_domain_fallback():
    state: dict = {}
    record_search_query(state, query="q", agent_name="SearchExecutor")
    assert state[SEARCH_QUERY_RECORDS_KEY][0]["domain"] == "SearchExecutor"


def test_get_search_query_records_defaults_to_empty_list():
    assert get_search_query_records(None) == []
    assert get_search_query_records({}) == []


def test_get_search_count_prefers_explicit_counter():
    state = {SEARCH_COUNT_KEY: 5, SEARCH_QUERY_RECORDS_KEY: []}
    assert get_search_count(state) == 5


def test_get_search_count_falls_back_to_records_length():
    state = {SEARCH_QUERY_RECORDS_KEY: [{"query": "a"}, {"query": "b"}]}
    assert get_search_count(state) == 2


def test_get_search_count_none_state_is_zero():
    assert get_search_count(None) == 0
