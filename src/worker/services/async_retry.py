"""Async and sync retry utility functions."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, TypeVar

from src.shared.logging_config import logger

T = TypeVar("T")


async def with_retry(
    coro_fn: Callable[[], Any],
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Any:
    """Execute async callable with exponential backoff retry."""
    delay = initial_delay
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_fn()
        except retry_exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                logger.warning(
                    f"Operation failed on final attempt {attempt}/{max_attempts}: {exc}"
                )
                raise
            logger.debug(
                f"Operation failed attempt {attempt}/{max_attempts}, retrying in {delay:.2f}s: {exc}"
            )
            await asyncio.sleep(delay)
            delay *= backoff_factor

    if last_exc:
        raise last_exc


def with_retry_sync(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    retry_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> T:
    """Execute sync callable with exponential backoff retry."""
    delay = initial_delay
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retry_exceptions as exc:
            last_exc = exc
            if attempt == max_attempts:
                logger.warning(
                    f"Sync operation failed on final attempt {attempt}/{max_attempts}: {exc}"
                )
                raise
            logger.debug(
                f"Sync operation failed attempt {attempt}/{max_attempts}, retrying in {delay:.2f}s: {exc}"
            )
            time.sleep(delay)
            delay *= backoff_factor

    if last_exc:
        raise last_exc


__all__ = ["with_retry", "with_retry_sync"]
