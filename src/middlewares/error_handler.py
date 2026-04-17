"""Error Handler Middleware - Global exception handling"""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Request, status
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware

from ..core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    BaseAppException,
    ConfigurationError,
    DatabaseError,
    ExternalServiceError,
    InputValidationException,
    RateLimitException,
    RepositoryError,
    ResourceNotFoundError,
    SafetyBlockException,
    ServiceError,
    StorageError,
    TimeoutException,
    ValidationError,
)


class GlobalErrorHandler(BaseHTTPMiddleware):
    """Middleware for handling exceptions globally"""

    async def dispatch(self, request: Request, call_next: Callable) -> JSONResponse:
        try:
            response = await call_next(request)
            return response
        except Exception as e:
            return self._handle_exception(e, request)

    def _handle_exception(self, exc: Exception, request: Request) -> JSONResponse:
        """Handle different types of exceptions"""

        # Generate timestamp for error tracking
        timestamp = datetime.now(UTC).isoformat()
        if isinstance(exc, ResourceNotFoundError):
            logger.warning(
                f"Resource not found: {exc.message}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={
                    "error": "NOT_FOUND",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(exc, ValidationError):
            logger.warning(
                f"Validation error: {exc.message}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "VALIDATION_ERROR",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(exc, InputValidationException):
            logger.warning(
                f"Input validation error: {exc.message}",
                extra={"path": request.url.path},
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "INPUT_VALIDATION_ERROR",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {
                        "path": request.url.path,
                        "field": exc.field,
                        "value": exc.value,
                    },
                },
            )

        elif isinstance(exc, SafetyBlockException):
            logger.warning(
                f"Safety block: {exc.message}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "error": "SAFETY_VIOLATION",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {
                        "path": request.url.path,
                        "categories": exc.categories,
                    },
                },
            )

        elif isinstance(exc, AuthenticationError):
            logger.warning(
                f"Authentication error: {exc.message}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "AUTHENTICATION_ERROR",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(exc, AuthorizationError):
            logger.warning(
                f"Authorization error: {exc.message}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "AUTHORIZATION_ERROR",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(exc, RateLimitException):
            logger.warning(
                f"Rate limit exceeded: {exc.message}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "RATE_LIMIT_EXCEEDED",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {
                        "path": request.url.path,
                        "retry_after": exc.retry_after,
                    },
                },
            )

        elif isinstance(exc, TimeoutException):
            logger.error(
                f"Timeout error: {exc.message}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                content={
                    "error": "OPERATION_TIMED_OUT",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(exc, ExternalServiceError):
            logger.error(
                f"External service error: {exc.message}",
                extra={"path": request.url.path},
            )
            return JSONResponse(
                status_code=status.HTTP_502_BAD_GATEWAY,
                content={
                    "error": "EXTERNAL_SERVICE_ERROR",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(
            exc, ServiceError | DatabaseError | StorageError | RepositoryError
        ):
            logger.error(
                f"Service error: {exc.message}",
                extra={"path": request.url.path},
                exc_info=True,
            )
            # Use the exception's own status_code so callers can signal 409, 503, etc.
            http_status = (
                exc.status_code
                if exc.status_code
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            return JSONResponse(
                status_code=http_status,
                content={
                    "error": "SERVICE_ERROR",
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(exc, ConfigurationError):
            logger.error(
                f"Configuration error: {exc.message}",
                extra={"path": request.url.path},
                exc_info=True,
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": "CONFIGURATION_ERROR",
                    "detail": "Server configuration error",
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        elif isinstance(exc, BaseAppException):
            # Generic catch-all for any other app exceptions
            logger.error(
                f"Application error: {exc.message}",
                extra={"path": request.url.path},
                exc_info=True,
            )
            error_code = exc.error_type.upper().replace(" ", "_")
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error": error_code,
                    "detail": exc.message,
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )

        else:
            # Unexpected errors
            logger.exception(
                f"Unexpected error: {str(exc)}", extra={"path": request.url.path}
            )
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": "INTERNAL_ERROR",
                    "detail": "An unexpected error occurred",
                    "timestamp": timestamp,
                    "metadata": {"path": request.url.path},
                },
            )


def error_handler_middleware(app):
    """Factory function to add error handler middleware"""
    app.add_middleware(GlobalErrorHandler)
