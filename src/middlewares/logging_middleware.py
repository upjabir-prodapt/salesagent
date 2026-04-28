"""Logging Middleware - Request/Response logging"""

import time
from collections.abc import Callable

from fastapi import Request, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware
from jose import jwt


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware for logging requests and responses with Trace Context and User Identity"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        
        # 1. Extract Cloud Trace Context
        # Format: "TRACE_ID/SPAN_ID;o=TRACE_TRUE"
        trace_header = request.headers.get("X-Cloud-Trace-Context", "")
        trace_id = trace_header.split("/")[0] if trace_header else None

        # 2. Extract IAP User (unverified just for logging context)
        iap_header = request.headers.get("x-goog-iap-jwt-assertion")
        user_email = "anonymous"
        if iap_header:
            try:
                claims = jwt.get_unverified_claims(iap_header)
                user_email = claims.get("email", "unknown")
            except Exception:
                pass

        # Use loguru context to inject these into all logs during this request
        with logger.contextualize(trace_id=trace_id, user=user_email):
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
