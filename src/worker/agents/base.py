"""Core agent abstractions: retry policy, error classification, and the
Agent template method that owns retry for every pipeline step.

Design intent (see IMPLEMENTATION_PLAN.md):
  - One root pipeline composed of independent Agent steps, not one root
    ADK agent containing sub-agents that share session state.
  - When an LLM call inside a step fails, only that step retries. There is
    no shared retry budget and no invocation-resume machinery.
  - Data passes between steps as typed dataclasses (see models.py), never
    through a shared mutable session/state dict.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types
from tenacity import AsyncRetrying, RetryCallState
from tenacity.wait import wait_base

from src.shared.logging_config import logger
from src.shared.retrying import (
    backoff_delay,
    build_async_retrying,
    retry_after_seconds,
)
from src.worker.runtime.pricing import extract_usage_counts

if TYPE_CHECKING:
    from .observers import Observer

TIn = TypeVar("TIn")
TOut = TypeVar("TOut")


class ErrorKind(StrEnum):
    """Normalized failure category used to decide whether to retry."""

    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    TRANSIENT = "TRANSIENT"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    SAFETY = "SAFETY"
    FATAL = "FATAL"


RETRYABLE_KINDS: frozenset[ErrorKind] = frozenset(
    {
        ErrorKind.RATE_LIMIT,
        ErrorKind.TIMEOUT,
        ErrorKind.TRANSIENT,
        ErrorKind.INVALID_OUTPUT,
    }
)

_RATE_LIMIT_MARKERS = ("resource_exhausted", "429", "quota", "rate limit")
_SAFETY_MARKERS = ("safety", "blocked_reason", "harm_category", "prohibited")
_INVALID_OUTPUT_MARKERS = (
    "missing_output",
    "invalid_output",
    "empty output",
    "no output",
    "validation failed",
)
_CONNECT_MARKERS = ("connect", "connection", "tls", "dns")


def classify(exc: BaseException) -> ErrorKind:
    """Single error classifier for the whole agent pipeline.

    Replaces the four separate classifiers that used to exist across
    runtime/resilience/errors.py, agents/retrying_agent.py, and
    domain/output_validation.py.
    """
    if isinstance(exc, asyncio.TimeoutError | TimeoutError):
        return ErrorKind.TIMEOUT
    if isinstance(exc, AgentError):
        return exc.kind

    detail = str(exc).lower()
    status_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if status_code in (429,) or any(m in detail for m in _RATE_LIMIT_MARKERS):
        return ErrorKind.RATE_LIMIT
    if status_code in (408, 504) or "timeout" in detail or "timed out" in detail:
        return ErrorKind.TIMEOUT
    if any(m in detail for m in _SAFETY_MARKERS):
        return ErrorKind.SAFETY
    if any(m in detail for m in _INVALID_OUTPUT_MARKERS):
        return ErrorKind.INVALID_OUTPUT
    if any(m in detail for m in _CONNECT_MARKERS) or status_code in (
        500,
        502,
        503,
    ):
        return ErrorKind.TRANSIENT
    return ErrorKind.FATAL


class AgentError(Exception):
    """Raised by a step's execute()/validate() to signal a classified failure."""

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind = ErrorKind.FATAL,
        agent_name: str = "",
        attempts: int = 0,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.agent_name = agent_name
        self.attempts = attempts
        self.cause = cause


class InvalidOutputError(AgentError):
    """Raised when a step's own validate() rejects the result."""

    def __init__(
        self, message: str, *, agent_name: str = "", attempts: int = 0
    ) -> None:
        super().__init__(
            message,
            kind=ErrorKind.INVALID_OUTPUT,
            agent_name=agent_name,
            attempts=attempts,
        )


class RetryPolicy:
    """Retry configuration for one Agent step.

    Each step owns its own policy instance -- there is no shared counter
    between steps or between "layers" of retry. This is what makes retry
    scoped strictly to the step that failed.

    The policy is pure configuration plus the backoff arithmetic; the
    loop that consumes it is tenacity (see ``Agent.run`` and
    ``src/shared/retrying.py``). ``should_retry()``/``delay_for()`` remain
    the single source of truth for *whether* and *how long*, so the same
    decisions drive both ``Agent.run`` and ``SearchExecutor``'s per-query
    loop.

    RATE_LIMIT gets its own, deliberately larger budget. A Vertex AI
    ``RESOURCE_EXHAUSTED`` is a per-minute quota window that needs up to
    60s to reset, so retrying it on the same ~1s/2s schedule used for a
    malformed model response just re-hammers the wall and gives up before
    the window rolls over. Those fields default to ``None``, meaning "no
    different from any other retryable error" -- the larger budget is
    opted into explicitly by ``build_research_pipeline()``, so a policy
    constructed directly (as every unit test does) behaves exactly as it
    always did.
    """

    __slots__ = (
        "max_attempts",
        "initial_delay",
        "max_delay",
        "exp_base",
        "jitter",
        "timeout",
        "retry_on",
        "rate_limit_max_attempts",
        "rate_limit_initial_delay",
        "rate_limit_max_delay",
        "max_elapsed",
        "respect_retry_after",
    )

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        initial_delay: float = 1.0,
        max_delay: float = 30.0,
        exp_base: float = 2.0,
        jitter: float = 0.3,
        timeout: float = 120.0,
        retry_on: frozenset[ErrorKind] = RETRYABLE_KINDS,
        rate_limit_max_attempts: int | None = None,
        rate_limit_initial_delay: float | None = None,
        rate_limit_max_delay: float | None = None,
        max_elapsed: float | None = None,
        respect_retry_after: bool = True,
    ) -> None:
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exp_base = exp_base
        self.jitter = jitter
        self.timeout = timeout
        self.retry_on = retry_on
        self.rate_limit_max_attempts = rate_limit_max_attempts
        self.rate_limit_initial_delay = rate_limit_initial_delay
        self.rate_limit_max_delay = rate_limit_max_delay
        self.max_elapsed = max_elapsed
        self.respect_retry_after = respect_retry_after

    # -- per-kind budgets -------------------------------------------------

    def max_attempts_for(self, kind: ErrorKind | None) -> int:
        """Attempt budget for *kind* (RATE_LIMIT may have its own)."""
        if kind is ErrorKind.RATE_LIMIT and self.rate_limit_max_attempts:
            return self.rate_limit_max_attempts
        return self.max_attempts

    def max_delay_for(self, kind: ErrorKind | None) -> float:
        if kind is ErrorKind.RATE_LIMIT and self.rate_limit_max_delay:
            return self.rate_limit_max_delay
        return self.max_delay

    @property
    def attempt_ceiling(self) -> int:
        """Upper bound on attempts across every kind combined.

        tenacity's ``stop`` is evaluated before the error is classified,
        so it can only bound the loop globally; the per-kind budget is
        enforced by the retry predicate (see :class:`_KindBudget`). The
        bound is therefore the SUM, not the max: a run can legitimately
        spend its whole rate-limit budget on 429s *and* its whole
        ordinary budget on validation failures. Using the max here
        silently capped the two together -- two upstream 429s left the
        ReportCompiler with zero revision attempts.
        """
        return self.max_attempts + (self.rate_limit_max_attempts or 0)

    # -- decisions --------------------------------------------------------

    def should_retry(self, kind: ErrorKind, attempt: int) -> bool:
        """True if *attempt* (1-based, the attempt that just failed) may retry."""
        return attempt < self.max_attempts_for(kind) and kind in self.retry_on

    def delay_for(self, attempt: int, kind: ErrorKind | None = None) -> float:
        """Exponential backoff with symmetric jitter, capped at max_delay."""
        initial = self.initial_delay
        if kind is ErrorKind.RATE_LIMIT and self.rate_limit_initial_delay:
            initial = self.rate_limit_initial_delay
        return backoff_delay(
            attempt,
            initial_delay=initial,
            max_delay=self.max_delay_for(kind),
            exp_base=self.exp_base,
            jitter=self.jitter,
        )

    # -- tenacity ---------------------------------------------------------

    def build_retrying(
        self,
        *,
        before_sleep: Callable[[RetryCallState], Any] | None = None,
        budget: _KindBudget | None = None,
    ) -> AsyncRetrying:
        """A fresh tenacity loop enforcing this policy.

        One factory, so every retry loop in the pipeline -- the step level
        in ``Agent.run`` and the per-query level in ``SearchExecutor`` --
        derives its stop/wait/predicate from the same policy object.

        *budget* is the per-kind attempt counter shared by the retry
        predicate and the wait strategy; pass one in to read its tallies
        afterwards, otherwise a private one is created.

        Always build per operation, never cache: tenacity stores the
        ``RetryCallState`` on the instance, and the search step runs
        several queries concurrently.
        """
        budget = budget if budget is not None else _KindBudget(self)
        return build_async_retrying(
            max_attempts=self.attempt_ceiling,
            wait=_PolicyWait(self, budget),
            should_retry=budget.should_retry,
            max_elapsed=self.max_elapsed,
            before_sleep=before_sleep,
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"RetryPolicy(max_attempts={self.max_attempts}, "
            f"initial_delay={self.initial_delay}, max_delay={self.max_delay}, "
            f"timeout={self.timeout}, "
            f"rate_limit_max_attempts={self.rate_limit_max_attempts}, "
            f"max_elapsed={self.max_elapsed})"
        )


class _KindBudget:
    """Per-``ErrorKind`` attempt counter for one retry loop.

    A single global counter conflates budgets that exist precisely
    because they are different. With ``max_attempts=3`` and
    ``rate_limit_max_attempts=6``, the sequence 429 / 429 / validation
    failure gave the ReportCompiler *zero* revision attempts: the two
    429s had advanced the global counter to 3, so the first validation
    failure was refused at ``3 < 3``. Since the whole point of the
    rate-limit budget is that a quota event should not cost the step its
    real work, that made the compiler's revision loop -- its entire
    reason for retrying -- collateral damage of the fix.

    Counting per kind gives each budget independently: up to 6 attempts
    spent on 429s AND up to 3 spent on validation failures.

    Instantiated per ``Agent.run`` call (and per query in
    ``SearchExecutor``), never shared between them.
    """

    __slots__ = ("_policy", "_counts", "_last_attempt")

    def __init__(self, policy: RetryPolicy) -> None:
        self._policy = policy
        self._counts: dict[ErrorKind, int] = {}
        self._last_attempt = -1

    def should_retry(self, exc: BaseException, attempt_number: int) -> bool:
        """Charge this failure to its kind, then decide.

        A ``BaseException`` that is not an ``Exception``
        (``CancelledError``, ``KeyboardInterrupt``) is never retried and
        never classified -- it propagates untouched, exactly as the
        original ``except Exception`` loop allowed.
        """
        if not isinstance(exc, Exception):
            return False
        kind = classify(exc)
        # Idempotent per attempt, so a repeated predicate evaluation
        # cannot double-charge the budget.
        if attempt_number != self._last_attempt:
            self._last_attempt = attempt_number
            self._counts[kind] = self._counts.get(kind, 0) + 1
        return self._policy.should_retry(kind, self._counts[kind])

    def count(self, kind: ErrorKind | None) -> int:
        if kind is None:
            return 0
        return self._counts.get(kind, 0)

    def summary(self) -> str:
        """Per-kind tallies, for the permanent-failure log line.

        Without this, a step that burned five 429s before dying on a
        validation failure logs only ``kind=INVALID_OUTPUT`` -- which is
        how the quota problem behind this whole change stayed invisible.
        """
        if not self._counts:
            return "none"
        return ", ".join(
            f"{kind}={count}" for kind, count in sorted(self._counts.items())
        )


class _PolicyWait(wait_base):
    """tenacity wait strategy backed by a :class:`RetryPolicy`.

    Classifies the failure so RATE_LIMIT can draw on its own backoff
    schedule, indexed by that kind's *own* attempt count rather than the
    global one -- otherwise a 429 arriving late in a loop would start
    part-way up the ladder (or straight at the cap) instead of at
    ``rate_limit_initial_delay``.

    Prefers the server's own ``Retry-After``/``RetryInfo`` hint when
    Vertex sends one, but never waits *less* than the policy's own
    backoff, so a short hint cannot turn into a hammer loop.

    tenacity evaluates ``retry`` then ``wait`` then ``stop``
    (``BaseRetrying._post_retry_check_actions``), so the counter the
    predicate just incremented is already visible here.
    """

    def __init__(self, policy: RetryPolicy, budget: _KindBudget | None = None) -> None:
        self._policy = policy
        self._budget = budget

    def __call__(self, retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None and outcome.failed else None
        kind = classify(exc) if exc is not None else None
        index = retry_state.attempt_number
        if self._budget is not None and kind is not None:
            index = max(self._budget.count(kind), 1)
        delay = self._policy.delay_for(index, kind)
        if exc is None or not self._policy.respect_retry_after:
            return delay
        hint = retry_after_seconds(exc)
        if hint is None:
            return delay
        return max(delay, min(hint, self._policy.max_delay_for(kind)))


_LOG_FIELD_MAX_CHARS = 2000
_LOG_RESULT_MAX_CHARS = 6000


def _truncate(value: str, limit: int = _LOG_FIELD_MAX_CHARS) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}... [truncated, {len(value)} chars total]"


def _format_result_for_log(result: Any) -> str:
    """Render an agent's typed result for the log without dumping huge
    fields (e.g. Report.markdown can be 40k+ chars) verbatim. Falls back
    to a length-capped repr() for anything that isn't a dataclass.
    """
    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        parts = []
        for f in dataclasses.fields(result):
            value = getattr(result, f.name)
            if isinstance(value, str):
                value = _truncate(value)
            parts.append(f"{f.name}={value!r}")
        rendered = f"{type(result).__name__}({', '.join(parts)})"
    else:
        rendered = repr(result)
    return _truncate(rendered, _LOG_RESULT_MAX_CHARS)


class Agent(ABC, Generic[TIn, TOut]):
    """Base class for every pipeline step. Owns retry; subclasses implement
    only execute() (the actual work) and, optionally, validate() (a gate
    applied to a successful result that can itself trigger a retry).

    run() is intentionally not overridable: retry behavior must be uniform
    across every step so a failure in one step can never affect another.
    """

    name: str = "Agent"
    retry: RetryPolicy = RetryPolicy()

    async def run(self, request: TIn, obs: Observer) -> TOut:
        """Run this step to success, or raise AgentError.

        The loop is tenacity's (``AsyncRetrying``); this method supplies
        the policy, the per-attempt timeout, and the Observer callbacks.
        A fresh ``AsyncRetrying`` is built per call because tenacity keeps
        its ``RetryCallState`` on the instance, so a shared one would
        interleave attempt numbers across concurrent jobs.
        """
        # Stashed so AdkAgentStep.execute() can report token usage without
        # widening the execute()/to_output() abstract method signatures.
        self._current_observer = obs
        policy = self.retry
        loop_started = time.monotonic()
        attempt = 0
        started = loop_started
        result: Any = None

        def _remaining_budget() -> float | None:
            if policy.max_elapsed is None:
                return None
            return max(0.0, policy.max_elapsed - (time.monotonic() - loop_started))

        def _attempt_timeout() -> float:
            """Per-attempt ceiling, clamped to the step's remaining budget.

            Without the clamp, ``max_elapsed`` would only stop the loop
            from *starting* another attempt -- an attempt already running
            could still overshoot it by its full timeout, which is how a
            pipeline of four steps with generous timeouts ends up
            overrunning the 1800s Cloud Tasks dispatch deadline.
            """
            remaining = _remaining_budget()
            if remaining is None:
                return policy.timeout
            return min(policy.timeout, remaining)

        def _on_before_sleep(retry_state: RetryCallState) -> None:
            outcome = retry_state.outcome
            exc = outcome.exception() if outcome is not None else None
            kind = classify(exc) if exc is not None else ErrorKind.FATAL
            delay = (
                retry_state.next_action.sleep
                if retry_state.next_action is not None
                else 0.0
            )
            obs.on_retry(self.name, retry_state.attempt_number, kind, delay)
            logger.warning(
                f"[{self.name}] attempt {retry_state.attempt_number} "
                f"({kind} {budget.count(kind)}/{policy.max_attempts_for(kind)}) "
                f"failed, retrying in {delay:.2f}s: {exc}"
            )

        budget = _KindBudget(policy)
        retrying = policy.build_retrying(before_sleep=_on_before_sleep, budget=budget)

        try:
            async for tenacity_attempt in retrying:
                attempt = tenacity_attempt.retry_state.attempt_number
                obs.on_start(self.name, attempt)
                started = time.monotonic()
                with tenacity_attempt:
                    result = await asyncio.wait_for(
                        self.execute(request), timeout=_attempt_timeout()
                    )
                    self.validate(result)
                state = tenacity_attempt.retry_state
                if state.outcome is not None and not state.outcome.failed:
                    # Hand the real value back to tenacity: the `with`
                    # block only records success/failure, not the result.
                    state.set_result(result)
        except Exception as exc:  # noqa: BLE001 - deliberately broad; classified below
            # reraise=True, so this is the original error from the final
            # attempt -- never a tenacity.RetryError.
            kind = classify(exc)
            elapsed = time.monotonic() - started
            obs.on_failure(self.name, attempt, kind, exc)
            logger.error(
                f"[{self.name}] failed permanently after {attempt} "
                f"attempt(s) in {elapsed:.2f}s: kind={kind} "
                f"attempts_by_kind=({budget.summary()}) error={exc}"
            )
            raise AgentError(
                f"{self.name} failed after {attempt} attempt(s): {exc}",
                kind=kind,
                agent_name=self.name,
                attempts=attempt,
                cause=exc,
            ) from exc
        else:
            elapsed = time.monotonic() - started
            obs.on_success(self.name, attempt, elapsed)
            logger.info(
                f"[AgentResponse] {self.name} succeeded (attempt {attempt}, "
                f"{elapsed:.2f}s): {_format_result_for_log(result)}"
            )
            return result

    @abstractmethod
    async def execute(self, request: TIn) -> TOut:
        """Do the actual work for one attempt. Raise on failure."""

    def validate(self, result: TOut) -> None:
        """Optional post-success gate. Raise AgentError/InvalidOutputError
        to reject a structurally-successful-but-unacceptable result and
        trigger another attempt of this same step.
        """
        return


class AdkAgentStep(Agent[TIn, TOut]):
    """An Agent step whose execute() drives exactly one ADK LlmAgent.

    Each attempt gets a brand new InMemorySessionService and a single-agent
    Runner -- proven (see IMPLEMENTATION_PLAN.md) to retry cleanly with no
    invocation-resume machinery: a failed attempt simply discards its
    session and the next attempt starts fresh. No state is shared between
    attempts, and no state is shared between different AdkAgentStep
    instances (i.e. between pipeline steps).
    """

    _USER_ID = "worker"

    @abstractmethod
    def build_agent(self) -> LlmAgent:
        """Construct the (stateless-safe) ADK LlmAgent for one attempt."""

    @abstractmethod
    def to_input(self, request: TIn) -> str:
        """Render the typed request into the single user message text."""

    @abstractmethod
    def to_output(self, raw: Any, usage: tuple[int, int]) -> TOut:
        """Convert the agent's raw output_key value into a typed result.

        *usage* is (input_tokens, output_tokens) captured from the last
        model response seen in this attempt's session.
        """

    async def execute(self, request: TIn) -> TOut:
        agent = self.build_agent()
        session_service = InMemorySessionService()
        session_id = f"{self.name}-{id(request)}-{time.monotonic_ns()}"
        runner = Runner(
            app_name=self.name, agent=agent, session_service=session_service
        )
        await session_service.create_session(
            app_name=self.name, user_id=self._USER_ID, session_id=session_id
        )

        message = genai_types.UserContent(
            parts=[genai_types.Part(text=self.to_input(request))]
        )

        input_tokens = 0
        output_tokens = 0
        async for event in runner.run_async(
            user_id=self._USER_ID,
            session_id=session_id,
            new_message=message,
        ):
            usage_metadata = getattr(event, "usage_metadata", None)
            if usage_metadata is not None:
                delta_in, delta_out = extract_usage_counts(usage_metadata)
                input_tokens += delta_in
                output_tokens += delta_out

        model_name = getattr(agent.model, "model", None) or str(agent.model)
        observer = getattr(self, "_current_observer", None)
        if observer is not None and (input_tokens or output_tokens):
            observer.on_usage(self.name, model_name, input_tokens, output_tokens)

        session = await session_service.get_session(
            app_name=self.name, user_id=self._USER_ID, session_id=session_id
        )
        state = session.state if session is not None else {}
        output_key = agent.output_key or f"{self.name.lower()}_output"
        raw = state.get(output_key)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            raise InvalidOutputError(
                f"{self.name} completed without populating output_key={output_key!r}",
                agent_name=self.name,
            )
        return self.to_output(raw, (input_tokens, output_tokens))


__all__ = [
    "ErrorKind",
    "RETRYABLE_KINDS",
    "classify",
    "AgentError",
    "InvalidOutputError",
    "RetryPolicy",
    "Agent",
    "AdkAgentStep",
]
