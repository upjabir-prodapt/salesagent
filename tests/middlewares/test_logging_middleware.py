from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request, Response
from opentelemetry.trace import SpanContext, TraceFlags

from src.middlewares.logging_middleware import LoggingMiddleware


@pytest.mark.asyncio
async def test_logging_middleware_success():
    app = MagicMock()
    middleware = LoggingMiddleware(app)

    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/test"
    request.headers = {}
    request.state = MagicMock()

    async def call_next(req):
        return Response(status_code=200)

    # We need to mock the dispatch method or the whole chain
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_logging_middleware_uses_valid_span_context():
    app = MagicMock()
    middleware = LoggingMiddleware(app)

    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/api"
    request.query_params = {}
    request.headers = {}
    request.client = MagicMock(host="127.0.0.1")

    span_context = SpanContext(
        trace_id=0x1234567890ABCDEF1234567890ABCDEF,
        span_id=0x1234567890ABCDEF,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    mock_span = MagicMock()
    mock_span.get_span_context.return_value = span_context

    async def call_next(req):
        return Response(status_code=200)

    with patch(
        "src.middlewares.logging_middleware.trace.get_current_span",
        return_value=mock_span,
    ):
        response = await middleware.dispatch(request, call_next)

    assert response.headers.get("X-Trace-ID") is not None


@pytest.mark.asyncio
async def test_logging_middleware_parses_cloud_trace_header():
    app = MagicMock()
    middleware = LoggingMiddleware(app)

    request = MagicMock(spec=Request)
    request.method = "POST"
    request.url.path = "/hook"
    request.query_params = {}
    request.headers = {"X-Cloud-Trace-Context": "abc123/456;o=1"}
    request.client = None

    mock_span = MagicMock()
    mock_span.get_span_context.return_value = MagicMock(is_valid=False)

    async def call_next(req):
        return Response(status_code=201)

    with patch(
        "src.middlewares.logging_middleware.trace.get_current_span",
        return_value=mock_span,
    ):
        response = await middleware.dispatch(request, call_next)

    assert response.headers.get("X-Trace-ID") == "abc123"
