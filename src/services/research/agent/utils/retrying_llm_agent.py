"""Custom ADK LlmAgent with bounded leaf-level retries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .....core.config import settings
from .....core.exceptions import AgentOutputError
from .....core.logging_config import logger
from .agent_contracts import get_output_key, is_tracked_agent
from .retry_errors import classify_error, is_leaf_retryable_exception
from .retry_state import (
    PIPELINE_RETRY_AGENT_KEY,
    clear_retry_flag,
    increment_retry_count,
    max_retries_exceeded,
    pop_retry_hint,
    prepare_agent_retry,
    set_retry_hint,
)


class RetryingLlmAgent(LlmAgent):
    """Retry model/LLM-flow failures before escalating to runner-level retry."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        logger.debug("Starting leaf execution with retry wrapper for %s", self.name)
        while True:
            try:
                async for event in super()._run_async_impl(ctx):
                    yield event
                self._clear_retry_artifacts(ctx)
                logger.debug("Leaf execution succeeded for %s", self.name)
                return
            except Exception as exc:
                if isinstance(exc, asyncio.CancelledError):
                    logger.debug("Leaf execution cancelled for %s", self.name)
                    raise
                if not self._should_retry_leaf(ctx, exc):
                    logger.warning(
                        "Leaf error for %s is not retryable; propagating: %s",
                        self.name,
                        exc,
                    )
                    raise

                state = self._session_state(ctx)
                if state is None:
                    logger.warning(
                        "Leaf retry for %s skipped because session state is unavailable",
                        self.name,
                    )
                    raise
                if max_retries_exceeded(state, self.name):
                    logger.error(
                        "Leaf retry exhausted for %s after %s configured attempts",
                        self.name,
                        settings.AGENT_RETRY_ATTEMPTS,
                    )
                    raise self._as_agent_output_error(exc) from exc

                attempt = increment_retry_count(state, self.name)
                output_key = prepare_agent_retry(state, self.name) or self._output_key()
                retry_hint = self._build_retry_hint(output_key, exc)
                set_retry_hint(state, self.name, retry_hint)

                wait_seconds = max(0, int(settings.AGENT_RETRY_WAIT_FIXED))
                logger.warning(
                    "Leaf retry for %s attempt=%s/%s output_key=%r error=%s",
                    self.name,
                    attempt,
                    settings.AGENT_RETRY_ATTEMPTS,
                    output_key,
                    exc,
                )
                if wait_seconds:
                    await asyncio.sleep(wait_seconds)

    def _should_retry_leaf(self, ctx: InvocationContext, exc: Exception) -> bool:
        if not is_leaf_retryable_exception(exc):
            return False
        if not is_tracked_agent(self.name):
            return False
        return self._session_state(ctx) is not None

    def _clear_retry_artifacts(self, ctx: InvocationContext) -> None:
        state = self._session_state(ctx)
        if state is None:
            return
        cleared_hint = pop_retry_hint(state, self.name)
        if cleared_hint:
            logger.debug("Cleared retry hint for %s", self.name)
        if state.get(PIPELINE_RETRY_AGENT_KEY) == self.name:
            clear_retry_flag(state)
            logger.debug("Cleared pipeline retry flag for %s", self.name)

    def _output_key(self) -> str:
        if self.output_key:
            return self.output_key
        return get_output_key(self.name) or f"{self.name.lower()}_output"

    def _as_agent_output_error(self, exc: Exception) -> AgentOutputError:
        output_key = self._output_key()
        error_class = classify_error(str(exc))
        if error_class == "AGENT_ERROR":
            error_class = "MODEL_ERROR"
        return AgentOutputError(
            (
                f"Leaf agent '{self.name}' failed before producing "
                f"required output '{output_key}': {exc}"
            ),
            agent_name=self.name,
            output_key=output_key,
            error_class=error_class,
        )

    def _build_retry_hint(self, output_key: str, exc: Exception) -> str:
        return (
            f"Previous attempt for {self.name} failed with error: {exc}. "
            f"Re-run from the beginning and ensure final output populates "
            f"'{output_key}'."
        )

    @staticmethod
    def _session_state(ctx: InvocationContext) -> dict[str, Any] | None:
        session = getattr(ctx, "session", None)
        state = getattr(session, "state", None)
        if isinstance(state, dict):
            return state
        return None
