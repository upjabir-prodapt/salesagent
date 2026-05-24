"""Tests for OpenTelemetry tracing decorators."""

from unittest.mock import MagicMock, patch

import pytest

from src.utils.tracing import job_attrs, traced, traced_with_context


@pytest.mark.asyncio
async def test_traced_async_records_exception():
    calls: list[str] = []

    @traced("test.async")
    async def failing(job_id: str) -> None:
        calls.append(job_id)
        raise ValueError("boom")

    with patch("src.utils.tracing._tracer") as mock_tracer:
        span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = span
        with pytest.raises(ValueError, match="boom"):
            await failing("job_1")
        span.record_exception.assert_called_once()


def test_traced_sync_sets_attributes():
    @traced("test.sync", attributes={"research.job_id": "static"})
    def work() -> str:
        return "ok"

    with patch("src.utils.tracing._tracer") as mock_tracer:
        span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = span
        assert work() == "ok"
        span.set_attribute.assert_any_call("research.job_id", "static")


def test_job_attrs_from_kwargs():
    import inspect

    def fn(job_id: str, company_name: str) -> None:
        pass

    bound = inspect.signature(fn).bind("j1", "Acme")
    assert job_attrs(bound) == {
        "research.job_id": "j1",
        "research.company_name": "Acme",
    }


@pytest.mark.asyncio
async def test_traced_with_context_uses_carrier():
    @traced_with_context("test.background")
    async def background(
        job_id: str,
        trace_context_headers: dict[str, str] | None = None,
    ) -> str:
        return job_id

    with (
        patch("src.utils.tracing.extract") as mock_extract,
        patch("src.utils.tracing._tracer") as mock_tracer,
    ):
        span = MagicMock()
        mock_tracer.start_as_current_span.return_value.__enter__.return_value = span
        result = await background("job_1", trace_context_headers={"traceparent": "00"})
        assert result == "job_1"
        mock_extract.assert_called_once()
        mock_tracer.start_as_current_span.assert_called_once()
