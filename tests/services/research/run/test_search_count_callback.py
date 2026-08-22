"""after_tool_callback must record every live search into session state."""

from __future__ import annotations

from types import SimpleNamespace

from src.services.research.agents.adk.callbacks.tool import after_tool_callback
from src.services.research.run.search_log import (
    SEARCH_QUERY_RECORDS_KEY,
    get_search_count,
)


def _tool_context(state: dict) -> SimpleNamespace:
    return SimpleNamespace(
        agent_name="market_analyst",
        callback_context=SimpleNamespace(agent_name="market_analyst", state=state),
    )


def test_search_agent_call_increments_search_count() -> None:
    state: dict = {}
    ctx = _tool_context(state)

    after_tool_callback(
        SimpleNamespace(name="google_search_agent"),
        {"request": "acme corp annual revenue"},
        ctx,
        {"results": [{"url": "https://acme.example/ir", "snippet": "revenue"}]},
    )

    assert get_search_count(state) == 1
    assert state[SEARCH_QUERY_RECORDS_KEY][0]["query"] == "acme corp annual revenue"


def test_search_recorded_even_when_no_entries_extracted() -> None:
    state: dict = {}
    after_tool_callback(
        SimpleNamespace(name="google_search"),
        {"query": "acme corp ebitda"},
        _tool_context(state),
        {},
    )

    assert get_search_count(state) == 1


def test_non_search_tool_does_not_count() -> None:
    state: dict = {}
    after_tool_callback(
        SimpleNamespace(name="colt_product_search"),
        {"query": "sd-wan"},
        _tool_context(state),
        {"results": []},
    )

    assert get_search_count(state) == 0


def test_multiple_searches_accumulate() -> None:
    state: dict = {}
    ctx = _tool_context(state)
    for i in range(3):
        after_tool_callback(
            SimpleNamespace(name="google_search"),
            {"query": f"acme news {i}"},
            ctx,
            {"results": [{"url": f"https://acme.example/{i}", "snippet": "s"}]},
        )

    assert get_search_count(state) == 3
