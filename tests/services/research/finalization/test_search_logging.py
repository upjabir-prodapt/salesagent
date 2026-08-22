"""Search query logging and search_count cost attribution."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.services.research.finalization.operations import (
    run_cost_attribution_op,
    run_search_log_op,
)
from src.services.research.run.search_log import (
    SEARCH_COUNT_KEY,
    get_search_count,
    record_search_query,
)


def _state_with_searches(count: int) -> dict:
    state: dict = {"company_name": "Acme Corp"}
    for i in range(count):
        record_search_query(
            state,
            query=f"acme revenue {i}",
            agent_name="market_analyst",
            entries=[{"url": f"https://acme.example/{i}", "snippet": "s"}],
        )
    return state


def test_record_search_query_counts_and_skips_blank() -> None:
    state = _state_with_searches(3)
    record_search_query(state, query="   ", agent_name="market_analyst")

    assert state[SEARCH_COUNT_KEY] == 3
    assert get_search_count(state) == 3


def test_get_search_count_falls_back_to_record_length() -> None:
    state = _state_with_searches(2)
    del state[SEARCH_COUNT_KEY]
    assert get_search_count(state) == 2


def test_get_search_count_zero_when_no_searches() -> None:
    assert get_search_count({}) == 0
    assert get_search_count(None) == 0


@pytest.mark.asyncio
async def test_search_log_op_flushes_rows_to_bigquery() -> None:
    state = _state_with_searches(2)
    insert = MagicMock(return_value=True)

    await run_search_log_op(
        job_id="job-1",
        session_state=state,
        insert_search_query_batch=insert,
    )

    insert.assert_called_once()
    rows = insert.call_args.args[0]
    assert len(rows) == 2
    assert {r["company_name"] for r in rows} == {"Acme Corp"}
    assert rows[0]["query"] == "acme revenue 0"
    assert rows[0]["query_hash"]
    assert json.loads(rows[0]["search_results"])[0]["url"] == (
        "https://acme.example/0"
    )
    assert rows[0]["search_date"]


@pytest.mark.asyncio
async def test_search_log_op_noop_without_searches() -> None:
    insert = MagicMock()
    await run_search_log_op(
        job_id="job-1",
        session_state={"company_name": "Acme Corp"},
        insert_search_query_batch=insert,
    )
    insert.assert_not_called()


@pytest.mark.asyncio
async def test_cost_attribution_includes_search_count_and_costs() -> None:
    state = _state_with_searches(4)
    insert = MagicMock(return_value=True)
    metrics = {
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "latency": 12.5,
        "cost_usd": 0.25,
        "temperature": 0.2,
    }

    await run_cost_attribution_op(
        job_id="job-1",
        session_state=state,
        metrics=metrics,
        insert_cost_attribution=insert,
    )

    kwargs = insert.call_args.kwargs
    assert kwargs["search_count"] == 4
    assert kwargs["search_cost_usd"] > 0
    assert kwargs["token_cost_usd"] == 0.25
    assert kwargs["total_cost_usd"] == pytest.approx(
        0.25 + kwargs["search_cost_usd"], abs=1e-6
    )


@pytest.mark.asyncio
async def test_cost_attribution_reports_zero_searches_not_null() -> None:
    insert = MagicMock(return_value=True)
    metrics = {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
        "latency": 1.0,
        "cost_usd": None,
        "temperature": None,
    }

    await run_cost_attribution_op(
        job_id="job-1",
        session_state={"agent_telemetry_records": []},
        metrics=metrics,
        insert_cost_attribution=insert,
    )

    kwargs = insert.call_args.kwargs
    assert kwargs["search_count"] == 0
    assert kwargs["search_cost_usd"] == 0.0
    assert kwargs["total_cost_usd"] == 0.0
