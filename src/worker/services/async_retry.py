"""Async and sync retry utility functions.

Thin, signature-compatible adapters over the shared tenacity engine in
``src/shared/retrying.py``. These used to be two hand-rolled
``for attempt in range(...)`` loops with no jitter; the call sites
(``services/finalization_ops.py``) are unchanged.

Keeping the wrappers rather than pointing callers straight at
``retry_async``/``retry_sync`` preserves the ``backoff_factor`` keyword
these five call sites already use, and gives the finalization side-ops a
name to log under.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.shared.retrying import retry_async, retry_sync

T = TypeVar("T")

# Finalization side-ops (PDF render, GCS upload, BigQuery batch insert)
# are not on the critical path of producing the report, so they get a
# tighter ceiling than an agent step: enough to ride out a transient
# error or a short 429, not enough to hold the Cloud Tasks request open.
_DEFAULT_MAX_DELAY = 30.0
_DEFAULT_JITTER = 0.3


async def with_retry(
    coro_fn: Callable[[], Awaitable[Any]],
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    label: str = "async operation",
) -> Any:
    """Execute async callable with exponential backoff retry."""
    return await retry_async(
        coro_fn,
        label=label,
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        max_delay=_DEFAULT_MAX_DELAY,
        exp_base=backoff_factor,
        jitter=_DEFAULT_JITTER,
        retry_exceptions=retry_exceptions,
    )


def with_retry_sync(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
    label: str = "sync operation",
) -> T:
    """Execute sync callable with exponential backoff retry."""
    return retry_sync(
        fn,
        label=label,
        max_attempts=max_attempts,
        initial_delay=initial_delay,
        max_delay=_DEFAULT_MAX_DELAY,
        exp_base=backoff_factor,
        jitter=_DEFAULT_JITTER,
        retry_exceptions=retry_exceptions,
    )


__all__ = ["with_retry", "with_retry_sync"]
