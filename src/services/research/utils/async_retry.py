"""Async retry helpers for research side operations."""

from __future__ import annotations

import asyncio


async def with_retry(coro_fn, retries: int = 1, delay: float = 3.0):
    """Simple async retry wrapper."""
    for attempt in range(retries + 1):
        try:
            return await coro_fn()
        except Exception:
            if attempt < retries:
                await asyncio.sleep(delay)
            else:
                raise


async def with_retry_sync(fn, retries: int = 1, delay: float = 3.0):
    """Simple sync-to-thread retry wrapper."""
    return await with_retry(lambda: asyncio.to_thread(fn), retries=retries, delay=delay)
