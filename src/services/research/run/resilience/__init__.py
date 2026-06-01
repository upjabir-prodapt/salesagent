"""Runner-level retry and resilience."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner_loop import run_runner_with_per_agent_retry

__all__ = [
    "AGENT_RETRY_COUNTS_KEY",
    "PIPELINE_RETRY_AGENT_KEY",
    "run_runner_with_per_agent_retry",
]


def __getattr__(name: str):
    if name == "run_runner_with_per_agent_retry":
        from .runner_loop import run_runner_with_per_agent_retry

        return run_runner_with_per_agent_retry
    if name in ("AGENT_RETRY_COUNTS_KEY", "PIPELINE_RETRY_AGENT_KEY"):
        from . import state

        return getattr(state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
