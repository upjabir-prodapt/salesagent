import inspect

import pytest

from src.shared.utils.tracing import job_attrs, traced, traced_with_context


def test_job_attrs_from_bound_arguments():
    def sample(job_id: str, company_name: str) -> None:
        pass

    bound = inspect.signature(sample).bind("job-1", "Acme")
    attrs = job_attrs(bound)
    assert attrs["research.job_id"] == "job-1"
    assert attrs["research.company_name"] == "Acme"


def test_traced_sync_success():
    calls: list[str] = []

    @traced("test.sync", attributes={"app": "sales-agent"})
    def work(x: int) -> int:
        calls.append("ran")
        return x + 1

    assert work(1) == 2
    assert calls == ["ran"]


@pytest.mark.asyncio
async def test_traced_async_records_exception():
    @traced("test.async", record_exception=True)
    async def fail() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await fail()


def test_traced_with_context_requires_async():
    with pytest.raises(TypeError, match="async functions only"):

        @traced_with_context("test.ctx")
        def sync_noop() -> None:
            pass


@pytest.mark.asyncio
async def test_traced_with_context_async():
    @traced_with_context(
        "test.ctx",
        context_kwarg="trace_context_headers",
        attributes=lambda _b: {"k": "v"},
    )
    async def run(trace_context_headers: dict | None = None) -> str:
        return "ok"

    assert await run(trace_context_headers={"traceparent": "00-abc-def-01"}) == "ok"
