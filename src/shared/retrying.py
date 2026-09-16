"""Tenacity-backed exponential-backoff retry, shared by every retrying
call site in the service.

Why this module exists
----------------------
Retry used to be re-implemented by hand in four independent places
(``Agent.run``'s ``while True`` loop, ``SearchExecutor._run_one``'s
per-query loop, ``services/async_retry.py``, and nothing at all around
the guardrail/evaluation LLM calls). Each had its own backoff shape, and
none of them waited long enough to survive a Vertex AI
``RESOURCE_EXHAUSTED`` (429) quota event: the per-minute quota window
needs up to 60s to reset, and the old stack gave up after ~55s total
having re-hammered the endpoint nine times.

This module is the single retry engine. It is deliberately generic -- it
knows nothing about ``ErrorKind`` or agents -- so it can sit underneath
both ``src/shared`` (guardrails) and ``src/worker`` (agents, evaluation,
finalization) without an import cycle. The agent-specific,
``ErrorKind``-aware policy is layered on top of it in
``src/worker/agents/base.py``.

Two things it adds over a plain ``tenacity.retry`` decorator:

*   **Server-directed waits.** ``retry_after_seconds()`` pulls the real
    retry hint off the exception -- a ``google.rpc.RetryInfo`` detail or
    a ``Retry-After`` response header -- and the wait strategy prefers it
    over blind exponential backoff. Vertex tells us when to come back;
    nothing in the old stack read it.
*   **A wall-clock budget.** Every retry loop can carry a
    ``max_elapsed`` ceiling, so a generous per-attempt budget can never
    add up to an overrun of the 1800s Cloud Tasks dispatch deadline.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryCallState,
    Retrying,
    stop_after_attempt,
)
from tenacity.stop import stop_base
from tenacity.wait import wait_base

from src.shared.logging_config import logger

T = TypeVar("T")

__all__ = [
    "backoff_delay",
    "retry_after_seconds",
    "wait_backoff",
    "stop_on_budget",
    "build_async_retrying",
    "build_sync_retrying",
    "retry_async",
    "retry_sync",
]


# ---------------------------------------------------------------------------
# Backoff arithmetic
# ---------------------------------------------------------------------------


def backoff_delay(
    attempt: int,
    *,
    initial_delay: float,
    max_delay: float,
    exp_base: float = 2.0,
    jitter: float = 0.3,
) -> float:
    """Exponential backoff with symmetric proportional jitter, capped.

    ``attempt`` is 1-based and names the attempt that just failed, so the
    first retry waits ``initial_delay``.

    The jitter is *symmetric and proportional* -- the returned delay lies
    in ``raw * (1 +/- jitter)``, clamped at 0 -- rather than tenacity's
    own ``wait_exponential_jitter``, which only ever adds a flat
    ``uniform(0, jitter)``. Symmetric jitter is what de-synchronises a
    fleet of workers that all hit the same quota wall in the same second;
    additive jitter bounded by one second cannot.
    """
    raw = min(initial_delay * (exp_base ** (attempt - 1)), max_delay)
    if jitter <= 0:
        return raw
    spread = raw * jitter
    return max(0.0, raw + random.uniform(-spread, spread))


# ---------------------------------------------------------------------------
# Server-directed retry hints
# ---------------------------------------------------------------------------

# "34s", "34.5s", "PT34S" (rare), or a bare number of seconds.
_DURATION_RE = re.compile(r"^(?:PT)?(\d+(?:\.\d+)?)S?$", re.IGNORECASE)

_RETRY_AFTER_HEADERS = ("retry-after", "x-ratelimit-reset-after")

# google.rpc.RetryInfo arrives as a `details` entry whose "@type" ends in
# this. Vertex populates it on RESOURCE_EXHAUSTED for quota errors that
# have a known reset time.
_RETRY_INFO_SUFFIX = "google.rpc.RetryInfo"

# A server hint far larger than any sane wait is more likely a parsing
# mistake or a daily-quota reset than something worth blocking a Cloud
# Tasks request on.
_MAX_TRUSTED_RETRY_AFTER = 300.0


def _parse_duration(value: Any) -> float | None:
    """Coerce a protobuf/HTTP duration into seconds."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        seconds = float(value)
    elif isinstance(value, str):
        match = _DURATION_RE.match(value.strip())
        if match is None:
            return None
        seconds = float(match.group(1))
    else:
        return None
    if seconds <= 0 or seconds > _MAX_TRUSTED_RETRY_AFTER:
        return None
    return seconds


def _retry_info_from_details(details: Any) -> float | None:
    """Walk an arbitrarily-shaped error body looking for a RetryInfo."""
    if isinstance(details, dict):
        type_url = details.get("@type")
        if isinstance(type_url, str) and type_url.endswith(_RETRY_INFO_SUFFIX):
            hint = _parse_duration(details.get("retryDelay"))
            if hint is not None:
                return hint
        for value in details.values():
            hint = _retry_info_from_details(value)
            if hint is not None:
                return hint
    elif isinstance(details, list | tuple):
        for item in details:
            hint = _retry_info_from_details(item)
            if hint is not None:
                return hint
    return None


def retry_after_seconds(exc: BaseException) -> float | None:
    """The server's own "come back in N seconds", if it sent one.

    Reads, in order of preference, a ``google.rpc.RetryInfo.retryDelay``
    from the error body (``google.genai.errors.APIError.details``) and
    then a ``Retry-After`` response header. Returns None when there is no
    usable hint, which is the common case -- callers fall back to
    ``backoff_delay()``.

    Deliberately total: a malformed hint must never break the retry loop
    that is trying to recover from an error.
    """
    try:
        hint = _retry_info_from_details(getattr(exc, "details", None))
        if hint is not None:
            return hint

        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        for name in _RETRY_AFTER_HEADERS:
            raw = None
            getter = getattr(headers, "get", None)
            if callable(getter):
                raw = getter(name) or getter(name.title())
            if raw is not None:
                hint = _parse_duration(raw)
                if hint is not None:
                    return hint
    except Exception:  # pragma: no cover - a hint is never worth raising for
        return None
    return None


# ---------------------------------------------------------------------------
# Tenacity strategies
# ---------------------------------------------------------------------------


class wait_backoff(wait_base):  # noqa: N801 - matches tenacity's own naming
    """Tenacity wait strategy: ``backoff_delay()``, but preferring the
    server's ``Retry-After``/``RetryInfo`` hint when one is present.

    Subclasses ``wait_base`` (rather than being a plain callable) so it
    composes with tenacity's ``+`` operator and can be nested inside
    ``wait_combine``/``wait_chain``, which invoke children by keyword.
    """

    def __init__(
        self,
        *,
        initial_delay: float,
        max_delay: float,
        exp_base: float = 2.0,
        jitter: float = 0.3,
        respect_retry_after: bool = True,
        delay_for: Callable[[int], float] | None = None,
    ) -> None:
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exp_base = exp_base
        self.jitter = jitter
        self.respect_retry_after = respect_retry_after
        # Lets a caller that already owns the backoff arithmetic (e.g.
        # RetryPolicy.delay_for, which is pinned by its own unit tests)
        # stay the single source of truth for the delay shape.
        self._delay_for = delay_for

    def _computed(self, attempt: int) -> float:
        if self._delay_for is not None:
            return self._delay_for(attempt)
        return backoff_delay(
            attempt,
            initial_delay=self.initial_delay,
            max_delay=self.max_delay,
            exp_base=self.exp_base,
            jitter=self.jitter,
        )

    def __call__(self, retry_state: RetryCallState) -> float:
        computed = self._computed(retry_state.attempt_number)
        if not self.respect_retry_after:
            return computed
        outcome = retry_state.outcome
        if outcome is None or not outcome.failed:
            return computed
        hint = retry_after_seconds(outcome.exception())
        if hint is None:
            return computed
        # Honour the server, but never wait *less* than our own backoff
        # says: a short Retry-After on a repeatedly-failing call would
        # otherwise turn into a tight hammer loop.
        return max(computed, min(hint, self.max_delay))


def _as_predicate(
    should_retry: Callable[[BaseException, int], bool],
) -> Callable[[RetryCallState], bool]:
    """Adapt a ``(exception, attempt_number) -> bool`` policy into the
    ``retry=`` slot.

    ``tenacity.retry_if_exception`` hands its predicate only the
    exception, but a per-error-kind attempt budget has to see the attempt
    number too -- so this uses the bare-callable form of ``retry=``,
    which receives the whole ``RetryCallState``.
    """

    def predicate(retry_state: RetryCallState) -> bool:
        outcome = retry_state.outcome
        if outcome is None or not outcome.failed:
            return False
        exc = outcome.exception()
        if exc is None:  # pragma: no cover - failed implies an exception
            return False
        return should_retry(exc, retry_state.attempt_number)

    return predicate


class stop_on_budget(stop_base):  # noqa: N801 - matches tenacity's naming
    """Stop before starting an attempt the wall-clock budget cannot pay for."""

    def __init__(
        self,
        budget: float,
        *,
        reserve: Callable[[], float] | None = None,
        time_source: Callable[[], float] = time.monotonic,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        self.budget = budget
        self._reserve = reserve
        self._time = time_source
        self._on_stop = on_stop
        self._started = time_source()

    def __call__(self, retry_state: RetryCallState) -> bool:
        if self.budget <= 0:
            return False
        reserve = self._reserve() if self._reserve is not None else 0.0
        elapsed = self._time() - self._started
        if elapsed + retry_state.upcoming_sleep + reserve > self.budget:
            if self._on_stop is not None:
                self._on_stop()
            return True
        return False


def _build_stop(
    max_attempts: int,
    max_elapsed: float | None,
    *,
    reserve: Callable[[], float] | None = None,
    time_source: Callable[[], float] = time.monotonic,
    on_budget_stop: Callable[[], None] | None = None,
):
    stop = stop_after_attempt(max_attempts)
    if max_elapsed is not None and max_elapsed > 0:
        stop = stop | stop_on_budget(
            max_elapsed,
            reserve=reserve,
            time_source=time_source,
            on_stop=on_budget_stop,
        )
    return stop


def build_async_retrying(
    *,
    max_attempts: int,
    wait: wait_base,
    should_retry: Callable[[BaseException, int], bool],
    max_elapsed: float | None = None,
    before: Callable[[RetryCallState], Any] | None = None,
    before_sleep: Callable[[RetryCallState], Any] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    reserve: Callable[[], float] | None = None,
    time_source: Callable[[], float] = time.monotonic,
    on_budget_stop: Callable[[], None] | None = None,
) -> AsyncRetrying:
    """One ``AsyncRetrying`` for one operation.

    A fresh instance per operation is mandatory, not stylistic:
    ``AsyncRetrying.__aiter__`` stores the ``RetryCallState`` on the
    *instance*, so two concurrent ``async for`` loops over a shared
    instance interleave their attempt numbers. The search step runs up to
    eight queries concurrently, so this matters here.
    """
    kwargs: dict[str, Any] = {
        "stop": _build_stop(
            max_attempts,
            max_elapsed,
            reserve=reserve,
            time_source=time_source,
            on_budget_stop=on_budget_stop,
        ),
        "wait": wait,
        "retry": _as_predicate(should_retry),
        "before_sleep": before_sleep,
        "reraise": True,
    }
    if before is not None:
        kwargs["before"] = before
    if sleep is not None:
        kwargs["sleep"] = sleep
    return AsyncRetrying(**kwargs)


def build_sync_retrying(
    *,
    max_attempts: int,
    wait: wait_base,
    should_retry: Callable[[BaseException, int], bool],
    max_elapsed: float | None = None,
    before_sleep: Callable[[RetryCallState], Any] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Retrying:
    """Sync counterpart of :func:`build_async_retrying`.

    Note the asymmetry tenacity imposes: ``Retrying`` calls its hooks and
    its ``sleep`` without awaiting, so both must be plain functions here,
    whereas ``AsyncRetrying`` requires ``sleep`` to be awaitable.
    """
    kwargs: dict[str, Any] = {
        "stop": _build_stop(max_attempts, max_elapsed),
        "wait": wait,
        "retry": _as_predicate(should_retry),
        "before_sleep": before_sleep,
        "reraise": True,
    }
    if sleep is not None:
        kwargs["sleep"] = sleep
    return Retrying(**kwargs)


# ---------------------------------------------------------------------------
# General-purpose helpers (non-agent call sites)
# ---------------------------------------------------------------------------


def _log_before_sleep(
    label: str, max_attempts: int
) -> Callable[[RetryCallState], None]:
    def hook(retry_state: RetryCallState) -> None:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None and outcome.failed else None
        sleep_for = retry_state.next_action.sleep if retry_state.next_action else 0.0
        logger.warning(
            f"[Retry] {label} attempt {retry_state.attempt_number}/{max_attempts} "
            f"failed, retrying in {sleep_for:.2f}s: {exc}"
        )

    return hook


async def retry_async(
    coro_fn: Callable[[], Awaitable[T]],
    *,
    label: str = "operation",
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exp_base: float = 2.0,
    jitter: float = 0.3,
    max_elapsed: float | None = None,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
    respect_retry_after: bool = True,
) -> T:
    """Run an async callable under tenacity exponential backoff.

    Raises the *original* exception (not ``tenacity.RetryError``) once the
    attempt budget or the wall-clock budget is spent.
    """
    retrying = build_async_retrying(
        max_attempts=max_attempts,
        wait=wait_backoff(
            initial_delay=initial_delay,
            max_delay=max_delay,
            exp_base=exp_base,
            jitter=jitter,
            respect_retry_after=respect_retry_after,
        ),
        should_retry=lambda exc, _attempt: isinstance(exc, retry_exceptions),
        max_elapsed=max_elapsed,
        before_sleep=_log_before_sleep(label, max_attempts),
    )

    async def _invoke() -> T:
        # Always hand tenacity a genuine coroutine function. It selects
        # sync vs async by inspecting the callable
        # (`_utils.is_coroutine_callable`), so a plain lambda that merely
        # *returns* an awaitable -- e.g. `lambda: asyncio.to_thread(fn)`
        # -- would be treated as sync and its coroutine returned
        # un-awaited, silently skipping every retry.
        return await coro_fn()

    return await retrying(_invoke)


def retry_sync(
    fn: Callable[[], T],
    *,
    label: str = "operation",
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exp_base: float = 2.0,
    jitter: float = 0.3,
    max_elapsed: float | None = None,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
    respect_retry_after: bool = True,
) -> T:
    """Blocking counterpart of :func:`retry_async`."""
    retrying = build_sync_retrying(
        max_attempts=max_attempts,
        wait=wait_backoff(
            initial_delay=initial_delay,
            max_delay=max_delay,
            exp_base=exp_base,
            jitter=jitter,
            respect_retry_after=respect_retry_after,
        ),
        should_retry=lambda exc, _attempt: isinstance(exc, retry_exceptions),
        max_elapsed=max_elapsed,
        before_sleep=_log_before_sleep(label, max_attempts),
    )
    return retrying(fn)
