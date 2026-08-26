import pytest

from src.worker.services.finalization_ops import run_telemetry_flush_op


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


@pytest.mark.asyncio
async def test_run_pdf_op_success():
    from src.worker.services.finalization_ops import run_pdf_op

    uploaded: list[tuple[str, bytes]] = []

    def generate_pdf(report: str) -> bytes:
        return b"%PDF"

    def upload_pdf(job_id: str, pdf_bytes: bytes) -> None:
        uploaded.append((job_id, pdf_bytes))

    available = await run_pdf_op(
        job_id="job-pdf",
        final_report="# Report",
        generate_pdf=generate_pdf,
        upload_pdf=upload_pdf,
    )

    assert available is True
    assert uploaded == [("job-pdf", b"%PDF")]


@pytest.mark.asyncio
async def test_run_evaluation_op_success():
    from src.worker.services.finalization_ops import run_evaluation_op

    calls: list[str] = []

    async def evaluate(job_id: str, report: str, state: dict):
        calls.append(job_id)
        return {"score": 1}

    await run_evaluation_op(
        job_id="job-eval",
        final_report="# Report",
        session_state={},
        update_status=lambda *args, **kwargs: None,
        evaluate=evaluate,
        upload_evaluation=lambda job_id, result: calls.append("upload"),
    )

    assert calls == ["job-eval", "upload"]


@pytest.mark.asyncio
async def test_run_cost_attribution_op_success():
    from src.worker.services.finalization_ops import run_cost_attribution_op

    captured: dict = {}

    def insert_cost_attribution(**kwargs):
        captured.update(kwargs)

    await run_cost_attribution_op(
        job_id="job-cost",
        session_state={},
        metrics={
            "temperature": 0.1,
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "latency": 1.5,
            "cost_usd": 0.02,
        },
        insert_cost_attribution=insert_cost_attribution,
    )

    assert captured["job_id"] == "job-cost"
    assert captured["total_tokens"] == 15


@pytest.mark.asyncio
async def test_telemetry_flush_no_records_is_noop():
    from src.worker.services.finalization_ops import run_telemetry_flush_op

    await run_telemetry_flush_op(
        job_id="job-empty",
        session_state={},
        insert_agent_telemetry_batch=lambda records: (_ for _ in ()).throw(
            AssertionError("should not insert")
        ),
        upload_deadletter_json=lambda *_: None,
    )
