"""Logging configuration for standard Python logging."""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from .config import settings

_CONTEXT_DEFAULTS = {
    "trace_id": "no-trace",
    "span_id": None,
    "trace_sampled": None,
    "user_email": "anonymous",
    "username": "anonymous",
    "business_unit": "none",
    "organization": "none",
}
_LOG_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)


def _get_context() -> dict[str, Any]:
    context = _LOG_CONTEXT.get()
    context = _CONTEXT_DEFAULTS.copy() if context is None else context.copy()
    try:
        span_context = trace.get_current_span().get_span_context()
        if span_context and span_context.is_valid:
            context["trace_id"] = format(span_context.trace_id, "032x")
            context["span_id"] = format(span_context.span_id, "016x")
            context["trace_sampled"] = bool(span_context.trace_flags.sampled)
    except Exception:
        pass
    return context


def gcp_json_formatter(record: dict[str, Any]) -> str:
    """Format dictionary-style record into GCP-compatible JSON."""
    extra = record.get("extra", {})
    payload = {
        "severity": record["level"].name,
        "timestamp": record["time"].isoformat(),
        "message": record["message"],
        "logging.googleapis.com/sourceLocation": {
            "file": record["file"].path,
            "line": record["line"],
            "function": record["function"],
        },
    }

    trace_id = extra.get("trace_id")
    span_id = extra.get("span_id")
    trace_sampled = extra.get("trace_sampled")

    if trace_id and trace_id != "no-trace":
        payload["logging.googleapis.com/trace"] = (
            f"projects/{settings.GOOGLE_CLOUD_PROJECT}/traces/{trace_id}"
        )
    if span_id:
        payload["logging.googleapis.com/spanId"] = span_id
    if trace_sampled is not None:
        payload["logging.googleapis.com/trace_sampled"] = bool(trace_sampled)

    for key, value in extra.items():
        if key not in {"trace_id", "span_id", "trace_sampled"}:
            payload[key] = value

    return json.dumps(payload) + "\n"


class _GCPJsonLogFormatter(logging.Formatter):
    """Emit structured JSON compatible with Cloud Logging."""

    _STANDARD_ATTRS = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._STANDARD_ATTRS
        }
        mapped_record = {
            "level": type("L", (), {"name": record.levelname})(),
            "time": datetime.fromtimestamp(record.created, tz=UTC),
            "message": record.getMessage(),
            "file": type("F", (), {"path": record.pathname})(),
            "line": record.lineno,
            "function": record.funcName,
            "extra": extra,
        }
        return gcp_json_formatter(mapped_record).rstrip("\n")


class _ContextFilter(logging.Filter):
    """Inject request/user/trace context into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = _get_context()
        for key, value in context.items():
            if not hasattr(record, key):
                setattr(record, key, value)
        return True


@contextlib.contextmanager
def contextualize(**kwargs: Any):
    """Context manager to inject variables into logs."""
    current = _LOG_CONTEXT.get()
    updated = _CONTEXT_DEFAULTS.copy() if current is None else current.copy()
    updated.update(kwargs)
    token = _LOG_CONTEXT.set(updated)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def setup_logging() -> None:
    """Configure root logging handlers and formatting."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_ContextFilter())
    if settings.DEBUG:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-8s | %(trace_id)s | %(user_email)s | "
                "%(name)s:%(funcName)s:%(lineno)d - %(message)s"
            )
        )
    else:
        handler.setFormatter(_GCPJsonLogFormatter())

    root_logger.addHandler(handler)
    logger.info(
        "Logging configured with level: %s (GCP correlation: %s)",
        settings.LOG_LEVEL,
        "OFF" if settings.DEBUG else "ON",
    )


logger = logging.getLogger("sales_agent")
