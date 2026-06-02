"""Custom ADK LlmAgent with bounded leaf-level retries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.agents.base_agent import BaseAgentState
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .....core.config import settings
from .....core.exceptions import AgentOutputError
from .....core.logging_config import logger
from ...domain.agent_contracts import get_output_key, is_tracked_agent
from ...run.resilience.errors import classify_error, is_leaf_retryable_exception
from ...run.resilience.state import (
    PIPELINE_RETRY_AGENT_KEY,
    clear_retry_flag,
    increment_retry_count,
    max_retries_exceeded,
    pop_retry_hint,
    prepare_agent_retry,
    set_retry_hint,
)
from ...run.state_mutation import is_mutable_state
from ..sales.tools.output_persistence import (
    has_nonempty_output,
    persist_output_from_session_events,
)

__all__ = ["RetryingLlmAgent"]


class RetryingLlmAgent(LlmAgent):
    """Retry model/LLM-flow and missing output_key failures before pipeline retry."""

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        logger.info(f"[Retry] Starting leaf execution for agent={self.name}")
        while True:
            try:
                async for event in super()._run_async_impl(ctx):
                    yield event
            except Exception as exc:
                if isinstance(exc, asyncio.CancelledError):
                    logger.debug(
                        f"[Retry] Leaf execution cancelled for agent={self.name}"
                    )
                    raise
                reason = self._leaf_retry_block_reason(ctx, exc)
                if reason is not None:
                    logger.warning(
                        f"[Retry] Leaf error for agent={self.name} not retried "
                        f"({reason}): {exc}"
                    )
                    raise

                state = self._session_state(ctx)
                if state is None or max_retries_exceeded(state, self.name):
                    logger.error(
                        f"[Retry] Leaf retry exhausted for agent={self.name} "
                        f"after {settings.AGENT_RETRY_ATTEMPTS} attempts"
                    )
                    raise self._as_agent_output_error(exc) from exc

                async for reset_event in self._reset_adk_agent_state(ctx):
                    yield reset_event
                await self._schedule_leaf_retry(ctx, state, exc)
                continue

            if not is_tracked_agent(self.name):
                self._clear_retry_artifacts(ctx)
                logger.info(f"[Retry] Leaf execution succeeded for agent={self.name}")
                return

            output_key = self._output_key()
            state = self._session_state(ctx)
            if state is None:
                self._clear_retry_artifacts(ctx)
                return

            self._persist_output_if_needed(ctx, state, output_key)
            if has_nonempty_output(state, output_key):
                self._clear_retry_artifacts(ctx)
                logger.info(f"[Retry] Leaf execution succeeded for agent={self.name}")
                return

            if max_retries_exceeded(state, self.name):
                logger.error(
                    f"[Retry] Leaf retry exhausted for agent={self.name} "
                    f"(missing output_key={output_key!r})"
                )
                raise self._as_missing_output_error()

            async for reset_event in self._reset_adk_agent_state(ctx):
                yield reset_event
            await self._schedule_leaf_retry(
                ctx,
                state,
                AgentOutputError(
                    (
                        f"{self.name} completed without populating required output "
                        f"'{output_key}'."
                    ),
                    agent_name=self.name,
                    output_key=output_key,
                    error_class="MISSING_OUTPUT",
                ),
                reason="missing_output_key",
            )

    async def _schedule_leaf_retry(
        self,
        ctx: InvocationContext,
        state: dict[str, Any],
        exc: Exception,
        *,
        reason: str = "exception",
    ) -> None:
        attempt = increment_retry_count(state, self.name)
        output_key = prepare_agent_retry(state, self.name) or self._output_key()
        if reason == "exception":
            retry_hint = self._build_retry_hint(output_key, exc)
            set_retry_hint(state, self.name, retry_hint)
        else:
            set_retry_hint(
                state,
                self.name,
                (
                    f"Previous attempt for {self.name} did not populate '{output_key}'. "
                    "Re-run and emit /*FINAL_ANSWER*/ with valid output."
                ),
            )

        wait_seconds = max(0, int(settings.AGENT_RETRY_WAIT_FIXED))
        logger.warning(
            f"[Retry] Leaf retry for agent={self.name} "
            f"attempt={attempt}/{settings.AGENT_RETRY_ATTEMPTS} "
            f"output_key={output_key!r} reason={reason} error={exc}"
        )
        if wait_seconds:
            await asyncio.sleep(wait_seconds)

    async def _reset_adk_agent_state(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        ctx.set_agent_state(self.name, agent_state=BaseAgentState())
        yield self._create_agent_state_event(ctx)

    def _persist_output_if_needed(
        self,
        ctx: InvocationContext,
        state: dict[str, Any],
        output_key: str,
    ) -> None:
        session = getattr(ctx, "session", None)
        events = getattr(session, "events", None) or []
        persist_output_from_session_events(
            state,
            events,
            agent_name=self.name,
            output_key=output_key,
            invocation_id=ctx.invocation_id,
        )

    def _leaf_retry_block_reason(
        self, ctx: InvocationContext, exc: Exception
    ) -> str | None:
        if not is_leaf_retryable_exception(exc):
            return "non-retryable error class"
        if not is_tracked_agent(self.name):
            return "untracked agent"
        if self._session_state(ctx) is None:
            return "session state unavailable"
        return None

    def _clear_retry_artifacts(self, ctx: InvocationContext) -> None:
        state = self._session_state(ctx)
        if state is None:
            return
        cleared_hint = pop_retry_hint(state, self.name)
        if cleared_hint:
            logger.debug(f"[Retry] Cleared retry hint for agent={self.name}")
        if state.get(PIPELINE_RETRY_AGENT_KEY) == self.name:
            clear_retry_flag(state)
            logger.debug(f"[Retry] Cleared pipeline retry flag for agent={self.name}")

    def _output_key(self) -> str:
        if self.output_key:
            return self.output_key
        return get_output_key(self.name) or f"{self.name.lower()}_output"

    def _as_missing_output_error(self) -> AgentOutputError:
        output_key = self._output_key()
        return AgentOutputError(
            (
                f"Leaf agent '{self.name}' exhausted retries without populating "
                f"required output '{output_key}'."
            ),
            agent_name=self.name,
            output_key=output_key,
            error_class="MISSING_OUTPUT",
        )

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
    def _session_state(ctx: InvocationContext) -> Any | None:
        session = getattr(ctx, "session", None)
        state = getattr(session, "state", None)
        if is_mutable_state(state):
            return state
        return None
