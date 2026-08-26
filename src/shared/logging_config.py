"""Logging configuration for standard Python logging.

Log message prefixes (grep-friendly):
  [Retry]       — leaf/runner retry decisions and attempts
  [Validation]  — output contract and guardrail failures
  [Persist]     — session output_key persistence
  [Pipeline]    — job/runner/orchestrator lifecycle
  [Callback]    — ADK callback hooks

Use LOG_LEVEL=DEBUG locally to see verbose skip paths. LOG_FILE mirrors stderr.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from opentelemetry import trace

from .config import settings

_CONTEXT_DEFAULTS: dict[str, Any] = {
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


def _build_formatter() -> logging.Formatter:
    if settings.DEBUG:
        return logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(trace_id)s | %(user_email)s | "
            "%(name)s:%(funcName)s:%(lineno)d - %(message)s"
        )
    return _GCPJsonLogFormatter()


def _build_stream_handler(stream: TextIO | None = None) -> logging.Handler:
    """Handler that always mirrors logs to the terminal."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.addFilter(_ContextFilter())
    handler.setFormatter(_build_formatter())
    return handler


def _build_file_handler(path: Path) -> logging.Handler:
    """Optional handler that mirrors the same logs to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler: logging.Handler = logging.FileHandler(path, encoding="utf-8")
    handler.addFilter(_ContextFilter())
    handler.setFormatter(_build_formatter())
    return handler


def setup_logging() -> None:
    """Configure root logging handlers and formatting."""
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    # Terminal is always required.
    root_logger.addHandler(_build_stream_handler(stream=sys.stderr))

    # When LOG_FILE is set, mirror the same logs to disk as well.
    log_path = settings.app_log_path
    if log_path is not None:
        root_logger.addHandler(_build_file_handler(log_path))

    logger.info(
        f"Logging configured with level: {settings.LOG_LEVEL} "
        f"(GCP correlation: {'OFF' if settings.DEBUG else 'ON'}"
        f"{f', file={log_path}' if log_path is not None else ', terminal only'})"
    )


logger = logging.getLogger("sales_agent")
