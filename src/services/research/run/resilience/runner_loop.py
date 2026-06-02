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
from ...domain.agent_contracts import (
    AGENT_OUTPUT_KEYS,
    get_output_key,
    is_tracked_agent,
)
from ...domain.output_validation import validate_agent_output
from .adk_resume import append_agent_reset_events
from .errors import agent_failure_from_event, resolve_retry_agents
from .state import StateMutator, apply_retry, clear_retry_flag, prepare_agents_retry

EventHandler = Callable[[Any], Awaitable[None] | None]
GetSession = Callable[[], Awaitable[Any]]

__all__ = [
    "AGENT_OUTPUT_KEYS",
    "get_output_key",
    "is_tracked_agent",
    "validate_agent_output",
    "build_retry_continuation_message",
    "run_runner_with_per_agent_retry",
]


def build_retry_continuation_message(
    agent_name: str,
    company_name: str | None = None,
    *,
    output_key: str | None = None,
    error_class: str | None = None,
    reason: str | None = None,
) -> types.Content:
    """Legacy cold continuation message (used only when requires_cold_retry is true)."""
    company = company_name or "the target company"
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
        f"[Retry] Building continuation message agent={agent_name} "
        f"error_class={error_class} output_key={output_key!r}"
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
    get_session: GetSession | None,
    on_retry: Callable[..., Awaitable[None] | None] | None,
    persist_state: StateMutator | None,
    company_name: str | None,
) -> tuple[str | None, types.Content | None]:
    """Apply retry bookkeeping; warm-resume invocation for failed agent(s) only."""
    from ..state_mutation import requires_cold_retry

    state = await get_session_state()
    logger.info(
        f"[Retry] Evaluating agent={exc.agent_name} "
        f"error_class={getattr(exc, 'error_class', None)} "
        f"current_retry_counts={state.get('agent_retry_counts')}"
    )
    if not await apply_retry(state, exc, on_retry, persist_state=persist_state):
        logger.error(
            f"[Retry] Denied for agent={exc.agent_name} "
            f"(exceeded retries or not tracked)"
        )
        raise exc

    agents_to_retry = resolve_retry_agents(exc, state)

    if requires_cold_retry(exc):
        logger.warning(
            f"[Retry] Cold retry for agent={exc.agent_name} via main app "
            f"(new invocation, not resuming {last_invocation_id})"
        )
        if persist_state is not None:

            def _clear_flag(target: dict[str, Any]) -> None:
                clear_retry_flag(target)
                prepare_agents_retry(target, agents_to_retry)

            persist_state(_clear_flag)
        else:
            clear_retry_flag(state)
            prepare_agents_retry(state, agents_to_retry)
        return None, build_retry_continuation_message(
            exc.agent_name,
            company_name,
            output_key=getattr(exc, "output_key", None),
            error_class=getattr(exc, "error_class", None),
            reason=str(exc),
        )

    if not last_invocation_id:
        logger.warning(
            f"[Retry] No invocation_id for warm resume agent={exc.agent_name}; "
            "starting continuation message"
        )
        return None, build_retry_continuation_message(
            exc.agent_name,
            company_name,
            output_key=getattr(exc, "output_key", None),
            error_class=getattr(exc, "error_class", None),
            reason=str(exc),
        )

    if get_session is not None:
        session = await get_session()
        if session is not None:
            append_agent_reset_events(
                session,
                agents_to_retry,
                invocation_id=last_invocation_id,
            )

    logger.info(
        f"[Retry] Warm retry for agents={agents_to_retry} "
        f"via invocation resume={last_invocation_id}"
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
    get_session: GetSession | None = None,
    on_retry: Callable[..., Awaitable[None] | None] | None = None,
    persist_state: StateMutator | None = None,
    company_name: str | None = None,
) -> None:
    """Run ADK runner on the main app; retry via warm resume for failed agent(s) only."""
    last_invocation_id: str | None = None
    initial_message = new_message
    is_first_iteration = True

    while True:
        run_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "run_config": run_config,
        }
        if last_invocation_id:
            logger.info(
                f"[Retry] Resuming ADK invocation {last_invocation_id} after agent failure"
            )
            run_kwargs["invocation_id"] = last_invocation_id
        elif initial_message is not None:
            if is_first_iteration:
                logger.info(
                    f"[Pipeline] Starting ADK runner session_id={session_id} "
                    f"app_name={app_name}"
                )
            else:
                logger.info(
                    f"[Retry] Starting new runner iteration with continuation message "
                    f"session_id={session_id}"
                )
            run_kwargs["new_message"] = initial_message
        else:
            raise ServiceError(
                "Cannot start runner: no message and no invocation to resume"
            )

        is_first_iteration = False

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
                    logger.warning(
                        f"[Pipeline] Untracked agent={author} failed: {detail}"
                    )
                    raise ServiceError(f"Agent '{author}' failed: {detail}")

                result = process_event(event)
                if asyncio.iscoroutine(result):
                    await result

            logger.info(
                f"[Pipeline] ADK runner finished successfully session_id={session_id}"
            )
            return

        except AgentOutputError as exc:
            logger.warning(
                f"[Retry] Agent output failure agent={exc.agent_name} "
                f"error_class={getattr(exc, 'error_class', None)}: {exc}"
            )
            last_invocation_id, initial_message = await _handle_agent_failure_retry(
                exc,
                last_invocation_id=last_invocation_id,
                get_session_state=get_session_state,
                get_session=get_session,
                on_retry=on_retry,
                persist_state=persist_state,
                company_name=company_name,
            )
        except Exception as exc:
            grouped_error = _extract_agent_output_error(exc)
            if grouped_error is None:
                logger.error(
                    f"[Pipeline] Unhandled runner exception session_id={session_id}: {exc}"
                )
                raise
            logger.warning(
                f"[Retry] Agent output failure from grouped exception "
                f"agent={grouped_error.agent_name} "
                f"error_class={getattr(grouped_error, 'error_class', None)}: "
                f"{grouped_error}"
            )
            last_invocation_id, initial_message = await _handle_agent_failure_retry(
                grouped_error,
                last_invocation_id=last_invocation_id,
                get_session_state=get_session_state,
                get_session=get_session,
                on_retry=on_retry,
                persist_state=persist_state,
                company_name=company_name,
            )


def _extract_agent_output_error(exc: Exception) -> AgentOutputError | None:
    if isinstance(exc, AgentOutputError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        logger.debug(
            f"[Retry] Inspecting ExceptionGroup for nested AgentOutputError: "
            f"{type(exc).__name__}"
        )
        for nested in exc.exceptions:
            if isinstance(nested, Exception):
                found = _extract_agent_output_error(nested)
                if found is not None:
                    logger.info(
                        f"[Retry] Recovered nested AgentOutputError from grouped "
                        f"exception agent={found.agent_name}"
                    )
                    return found
    return None
