"""Logging Middleware - Request/Response logging"""

import time
from collections.abc import Callable

from fastapi import Request, Response
from opentelemetry import trace
from starlette.middleware.base import BaseHTTPMiddleware

from src.shared.logging_config import contextualize, logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging requests and responses with Trace Context and User Identity"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()

        # 1. Extract Cloud Trace Context from OpenTelemetry
        current_span = trace.get_current_span()
        span_context = current_span.get_span_context()

        trace_id = None
        span_id = None

        if span_context and span_context.is_valid:
            trace_id = format(span_context.trace_id, "032x")
            span_id = format(span_context.span_id, "016x")
            trace_sampled = bool(span_context.trace_flags.sampled)
        else:
            trace_sampled = None

        # Fallback to header if trace_id still None (e.g. OTel failed to instrument)
        if not trace_id:
            trace_header = request.headers.get("X-Cloud-Trace-Context", "")
            if trace_header:
                parts = trace_header.split("/")
                trace_id = parts[0]
                if len(parts) > 1 and ";" in parts[1]:
                    span_id = parts[1].split(";")[0].split(":")[0]  # Handle hex span id

        # 2. Extract User Identity (will be populated by the route if available)
        user_email = "anonymous"
        username = "anonymous"
        business_unit = "none"
        organization = "none"

        # Inject request-scoped context into standard logging records.
        with contextualize(
            trace_id=trace_id,
            span_id=span_id,
            trace_sampled=trace_sampled,
            user_email=user_email,
            username=username,
            business_unit=business_unit,
            organization=organization,
        ):
            logger.info(
                "Request started",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "query_params": dict(request.query_params),
                    "client_host": request.client.host if request.client else None,
                },
            )

            # Process request
            response = await call_next(request)

            # Calculate duration
            duration = time.time() - start_time

            # Log response
            logger.info(
                "Request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_seconds": round(duration, 3),
                },
            )

        # Add metadata to response headers
        response.headers["X-Process-Time"] = str(round(duration, 3))
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id

        return response


def logging_middleware(app):
    """Factory function to add logging middleware"""
    app.add_middleware(LoggingMiddleware)
