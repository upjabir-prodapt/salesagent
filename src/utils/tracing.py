"""OpenTelemetry span decorators for service-layer methods."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from opentelemetry import trace
from opentelemetry.propagate import extract

_tracer = trace.get_tracer(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def _apply_attributes(span: trace.Span, attributes: Any, bound: inspect.BoundArguments) -> None:
    if attributes is None:
        return
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            if value is not None:
                span.set_attribute(key, value)
        return
    if callable(attributes):
        for key, value in attributes(bound).items():
            if value is not None:
                span.set_attribute(key, value)


def job_attrs(bound: inspect.BoundArguments) -> dict[str, Any]:
    """Common research span attributes from job_id / company_name kwargs."""
    return {
        "research.job_id": bound.arguments.get("job_id"),
        "research.company_name": bound.arguments.get("company_name"),
    }


def traced(
    span_name: str,
    *,
    attributes: dict[str, Any] | Callable[[inspect.BoundArguments], dict[str, Any]] | None = None,
    record_exception: bool = True,
) -> Callable[[F], F]:
    """Create a child span around a sync or async function."""

    def decorator(func: F) -> F:
        signature = inspect.signature(func)

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                bound = signature.bind_partial(*args, **kwargs)
                with _tracer.start_as_current_span(span_name) as span:
                    _apply_attributes(span, attributes, bound)
                    try:
                        return await func(*args, **kwargs)
                    except Exception as exc:
                        if record_exception:
                            span.record_exception(exc)
                        raise

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            with _tracer.start_as_current_span(span_name) as span:
                _apply_attributes(span, attributes, bound)
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if record_exception:
                        span.record_exception(exc)
                    raise

        return sync_wrapper  # type: ignore[return-value]

    return decorator


def traced_with_context(
    span_name: str,
    *,
    context_kwarg: str = "trace_context_headers",
    attributes: dict[str, Any] | Callable[[inspect.BoundArguments], dict[str, Any]] | None = None,
    record_exception: bool = True,
) -> Callable[[F], F]:
    """Create a span linked to an upstream trace via W3C carrier headers."""

    def decorator(func: F) -> F:
        if not inspect.iscoroutinefunction(func):
            raise TypeError("traced_with_context supports async functions only")
        signature = inspect.signature(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            bound = signature.bind_partial(*args, **kwargs)
            carrier = bound.arguments.get(context_kwarg)
            parent_context = extract(carrier=carrier) if carrier else None
            with _tracer.start_as_current_span(
                span_name, context=parent_context
            ) as span:
                _apply_attributes(span, attributes, bound)
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    if record_exception:
                        span.record_exception(exc)
                    raise

        return async_wrapper  # type: ignore[return-value]

    return decorator
