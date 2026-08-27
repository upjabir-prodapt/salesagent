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
import random
import time
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from src.shared.logging_config import logger
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
    """Immutable retry configuration for one Agent step.

    Each step owns its own policy instance -- there is no shared counter
    between steps or between "layers" of retry. This is what makes retry
    scoped strictly to the step that failed.
    """

    __slots__ = (
        "max_attempts",
        "initial_delay",
        "max_delay",
        "exp_base",
        "jitter",
        "timeout",
        "retry_on",
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
    ) -> None:
        self.max_attempts = max_attempts
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exp_base = exp_base
        self.jitter = jitter
        self.timeout = timeout
        self.retry_on = retry_on

    def should_retry(self, kind: ErrorKind, attempt: int) -> bool:
        """True if *attempt* (1-based, the attempt that just failed) may retry."""
        return attempt < self.max_attempts and kind in self.retry_on

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with symmetric jitter, capped at max_delay."""
        raw = min(self.initial_delay * (self.exp_base ** (attempt - 1)), self.max_delay)
        if self.jitter <= 0:
            return raw
        spread = raw * self.jitter
        return max(0.0, raw + random.uniform(-spread, spread))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"RetryPolicy(max_attempts={self.max_attempts}, "
            f"initial_delay={self.initial_delay}, max_delay={self.max_delay}, "
            f"timeout={self.timeout})"
        )


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
        # Stashed so AdkAgentStep.execute() can report token usage without
        # widening the execute()/to_output() abstract method signatures.
        self._current_observer = obs
        attempt = 0
        while True:
            attempt += 1
            obs.on_start(self.name, attempt)
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self.execute(request), timeout=self.retry.timeout
                )
                self.validate(result)
            except Exception as exc:  # noqa: BLE001 - deliberately broad; classified below
                kind = classify(exc)
                elapsed = time.monotonic() - started
                if not self.retry.should_retry(kind, attempt):
                    obs.on_failure(self.name, attempt, kind, exc)
                    logger.error(
                        f"[{self.name}] failed permanently after {attempt} "
                        f"attempt(s) in {elapsed:.2f}s: kind={kind} error={exc}"
                    )
                    raise AgentError(
                        f"{self.name} failed after {attempt} attempt(s): {exc}",
                        kind=kind,
                        agent_name=self.name,
                        attempts=attempt,
                        cause=exc,
                    ) from exc
                delay = self.retry.delay_for(attempt)
                obs.on_retry(self.name, attempt, kind, delay)
                logger.warning(
                    f"[{self.name}] attempt {attempt}/{self.retry.max_attempts} "
                    f"failed (kind={kind}), retrying in {delay:.2f}s: {exc}"
                )
                await asyncio.sleep(delay)
                continue
            else:
                obs.on_success(self.name, attempt, time.monotonic() - started)
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
]
