"""Pipeline output validation and per-agent retry helpers for SalesResearchAgent."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types

from .....core.exceptions import AgentOutputError, ServiceError
from .....core.logging_config import logger
from .agent_contracts import (
    AGENT_OUTPUT_KEYS,
    get_output_key,
    is_tracked_agent,
    validate_agent_output,
)
from .retry_errors import agent_failure_from_event
from .retry_state import (
    StateMutator,
    apply_retry,
    clear_retry_flag,
)

EventHandler = Callable[[Any], Awaitable[None] | None]


def build_retry_continuation_message(
    agent_name: str,
    company_name: str | None = None,
    *,
    output_key: str | None = None,
    error_class: str | None = None,
    reason: str | None = None,
) -> types.Content:
    company = company_name or "the target company"
    retry_mode = "cold-missing-output" if error_class == "MISSING_OUTPUT" else "cold-generic"
    if error_class == "MISSING_OUTPUT" and output_key:
        detail = (
            f"{agent_name} completed without populating required output_key "
            f"'{output_key}'"
        )
        instructions = (
            f"Re-run {agent_name} from the beginning, produce a complete answer, "
            f"and ensure the final response populates '{output_key}' by emitting "
            "/*FINAL_ANSWER*/ with valid output."
        )
    else:
        detail = f"Agent {agent_name} failed or produced incomplete output"
        if reason:
            detail = f"{detail}: {reason}"
        instructions = (
            f"Re-run only what is needed for {agent_name} and continue the pipeline. "
            "Emit /*FINAL_ANSWER*/ with valid output when done."
        )
    logger.info(
        "Building retry continuation message mode=%s agent=%s error_class=%s output_key=%r",
        retry_mode,
        agent_name,
        error_class,
        output_key,
    )
    return types.UserContent(
        parts=[
            types.Part(
                text=(
                    f"Continue the sales research for {company}. "
                    f"{detail}. "
                    f"{instructions}"
                )
            )
        ]
    )


async def _handle_agent_failure_retry(
    exc: AgentOutputError,
    *,
    last_invocation_id: str | None,
    get_session_state: Callable[[], Awaitable[dict[str, Any]]],
    on_retry: Callable[..., Awaitable[None] | None] | None,
    persist_state: StateMutator | None,
    company_name: str | None,
) -> tuple[str | None, types.Content | None]:
    """Apply retry bookkeeping; resume invocation or start a new one on the main app."""
    from ...session_state_mutator import requires_cold_retry

    state = await get_session_state()
    logger.debug(
        "Evaluating retry for agent=%s error_class=%s current_retry_counts=%s",
        exc.agent_name,
        getattr(exc, "error_class", None),
        state.get("agent_retry_counts"),
    )
    if not await apply_retry(state, exc, on_retry, persist_state=persist_state):
        logger.error(
            "Retry denied for agent=%s (exceeded retries or not tracked)",
            exc.agent_name,
        )
        raise exc

    if requires_cold_retry(exc):
        logger.warning(
            "Cold retry for %s via main app (new invocation, not resuming %s)",
            exc.agent_name,
            last_invocation_id,
        )
        if persist_state is not None:
            persist_state(clear_retry_flag)
        else:
            clear_retry_flag(state)
        return None, build_retry_continuation_message(
            exc.agent_name,
            company_name,
            output_key=getattr(exc, "output_key", None),
            error_class=getattr(exc, "error_class", None),
            reason=str(exc),
        )

    logger.info(
        "Warm retry for %s via invocation resume=%s",
        exc.agent_name,
        last_invocation_id,
    )
    if persist_state is not None:
        persist_state(clear_retry_flag)
    else:
        clear_retry_flag(state)
    return last_invocation_id, None


async def run_runner_with_per_agent_retry(
    runner: Runner,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    run_config: RunConfig,
    new_message: types.Content | None,
    process_event: EventHandler,
    get_session_state: Callable[[], Awaitable[dict[str, Any]]],
    on_retry: Callable[..., Awaitable[None] | None] | None = None,
    persist_state: StateMutator | None = None,
    company_name: str | None = None,
) -> None:
    """Run ADK runner on the main app; retry via resume or new message on the same app."""
    last_invocation_id: str | None = None
    initial_message = new_message

    while True:
        run_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "run_config": run_config,
        }
        if last_invocation_id:
            logger.info(
                "Resuming ADK invocation %s after agent failure", last_invocation_id
            )
            run_kwargs["invocation_id"] = last_invocation_id
        elif initial_message is not None:
            run_kwargs["new_message"] = initial_message
        else:
            raise ServiceError("Cannot start runner: no message and no invocation to resume")

        try:
            async for event in runner.run_async(**run_kwargs):
                last_invocation_id = event.invocation_id

                error_code = getattr(event, "error_code", None)
                error_message = getattr(event, "error_message", None)
                if error_code or error_message:
                    author = getattr(event, "author", "unknown_agent")
                    detail = error_message or f"error_code={error_code}"
                    if is_tracked_agent(author):
                        raise agent_failure_from_event(author, detail)
                    raise ServiceError(f"Agent '{author}' failed: {detail}")

                result = process_event(event)
                if asyncio.iscoroutine(result):
                    await result

            return

        except AgentOutputError as exc:
            logger.warning(
                "Agent output failure agent=%s error_class=%s: %s",
                exc.agent_name,
                getattr(exc, "error_class", None),
                exc,
            )
            last_invocation_id, initial_message = await _handle_agent_failure_retry(
                exc,
                last_invocation_id=last_invocation_id,
                get_session_state=get_session_state,
                on_retry=on_retry,
                persist_state=persist_state,
                company_name=company_name,
            )
        except Exception as exc:
            grouped_error = _extract_agent_output_error(exc)
            if grouped_error is None:
                raise
            logger.warning(
                "Agent output failure from grouped exception agent=%s error_class=%s: %s",
                grouped_error.agent_name,
                getattr(grouped_error, "error_class", None),
                grouped_error,
            )
            last_invocation_id, initial_message = await _handle_agent_failure_retry(
                grouped_error,
                last_invocation_id=last_invocation_id,
                get_session_state=get_session_state,
                on_retry=on_retry,
                persist_state=persist_state,
                company_name=company_name,
            )


def _extract_agent_output_error(exc: Exception) -> AgentOutputError | None:
    if isinstance(exc, AgentOutputError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        logger.debug(
            "Inspecting ExceptionGroup for nested AgentOutputError: %s",
            type(exc).__name__,
        )
        for nested in exc.exceptions:
            if isinstance(nested, Exception):
                found = _extract_agent_output_error(nested)
                if found is not None:
                    logger.info(
                        "Recovered nested AgentOutputError from grouped exception agent=%s",
                        found.agent_name,
                    )
                    return found
    return None
