import pytest

from src.services.research.finalization.operations import run_telemetry_flush_op


@pytest.mark.asyncio
async def test_telemetry_flush_deadletters_on_failure():
    captured = {"deadletter": None}

    def insert_batch(_records):
        raise RuntimeError("bq insert failed")

    def upload_deadletter(name, payload):
        captured["deadletter"] = (name, payload)

    session_state = {
        "agent_telemetry_records": [{"agent_name": "ExecutiveAgent", "cost_usd": 0.1}]
    }

    with pytest.raises(RuntimeError):
        await run_telemetry_flush_op(
            job_id="job-123",
            session_state=session_state,
            insert_agent_telemetry_batch=insert_batch,
            upload_deadletter_json=upload_deadletter,
        )

    assert captured["deadletter"] is not None
    deadletter_name, deadletter_payload = captured["deadletter"]
    assert deadletter_name == "job-123_telemetry_deadletter"
    assert deadletter_payload["records"]
