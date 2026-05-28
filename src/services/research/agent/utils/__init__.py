"""Shared ADK runtime utilities (callbacks, pipeline, telemetry, safety)."""

from __future__ import annotations

__all__ = [
    "TELEMETRY_RECORDS_KEY",
    "after_agent_callback",
    "after_model_callback",
    "after_tool_callback",
    "before_agent_callback",
    "before_model_callback",
    "before_tool_callback",
    "log_event",
    "run_runner_with_per_agent_retry",
    "track_agent_end",
    "track_agent_start",
]


def __getattr__(name: str):
    if name == "log_event":
        from .agent import log_event

        return log_event
    if name == "run_runner_with_per_agent_retry":
        from .agent_pipeline import run_runner_with_per_agent_retry

        return run_runner_with_per_agent_retry
    if name in {
        "before_model_callback",
        "after_model_callback",
        "before_agent_callback",
        "after_agent_callback",
        "before_tool_callback",
        "after_tool_callback",
    }:
        from . import callbacks as _callbacks

        return getattr(_callbacks, name)
    if name in {"TELEMETRY_RECORDS_KEY", "track_agent_start", "track_agent_end"}:
        from . import telemetry as _telemetry

        return getattr(_telemetry, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
