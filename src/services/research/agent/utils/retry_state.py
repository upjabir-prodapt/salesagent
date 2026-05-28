"""Retry bookkeeping and state mutation primitives."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .....core.config import settings
from .....core.exceptions import AgentOutputError
from .....core.logging_config import logger
from .agent_contracts import get_output_key, is_tracked_agent

AGENT_RETRY_COUNTS_KEY = "agent_retry_counts"
PIPELINE_RETRY_AGENT_KEY = "pipeline_retry_agent"
AGENT_RETRY_HINTS_KEY = "agent_retry_hints"

StateMutator = Callable[[Callable[[dict[str, Any]], None]], None]


def get_retry_count(state: dict[str, Any], agent_name: str) -> int:
    counts = state.get(AGENT_RETRY_COUNTS_KEY) or {}
    if not isinstance(counts, dict):
        return 0
    return int(counts.get(agent_name, 0))


def increment_retry_count(state: dict[str, Any], agent_name: str) -> int:
    counts = dict(state.get(AGENT_RETRY_COUNTS_KEY) or {})
    counts[agent_name] = get_retry_count(state, agent_name) + 1
    state[AGENT_RETRY_COUNTS_KEY] = counts
    return counts[agent_name]


def max_retries_exceeded(state: dict[str, Any], agent_name: str) -> bool:
    if settings.AGENT_RETRY_ATTEMPTS <= 1:
        return get_retry_count(state, agent_name) >= 1
    return get_retry_count(state, agent_name) >= settings.AGENT_RETRY_ATTEMPTS - 1


def prepare_agent_retry(state: dict[str, Any], agent_name: str) -> str | None:
    output_key = get_output_key(agent_name)
    if output_key:
        state.pop(output_key, None)
    if agent_name == "ReportCompiler":
        state.pop("final_report", None)
        state.pop("report_validation_status", None)
        state.pop("report_validation_violations", None)

    agent_status_map = dict(state.get("agent_status_map") or {})
    agent_status_map[agent_name] = "retrying"
    state["agent_status_map"] = agent_status_map
    state[PIPELINE_RETRY_AGENT_KEY] = agent_name

    logger.warning(
        "Preparing retry for %s (attempt %s/%s), cleared output key %r",
        agent_name,
        get_retry_count(state, agent_name),
        settings.AGENT_RETRY_ATTEMPTS,
        output_key,
    )
    return output_key


def clear_retry_flag(state: dict[str, Any]) -> None:
    state.pop(PIPELINE_RETRY_AGENT_KEY, None)


def set_retry_hint(state: dict[str, Any], agent_name: str, hint: str) -> None:
    hints = dict(state.get(AGENT_RETRY_HINTS_KEY) or {})
    hints[agent_name] = hint
    state[AGENT_RETRY_HINTS_KEY] = hints
    logger.debug("Stored retry hint for %s", agent_name)


def pop_retry_hint(state: dict[str, Any], agent_name: str) -> str | None:
    hints = dict(state.get(AGENT_RETRY_HINTS_KEY) or {})
    value = hints.pop(agent_name, None)
    if hints:
        state[AGENT_RETRY_HINTS_KEY] = hints
    else:
        state.pop(AGENT_RETRY_HINTS_KEY, None)
    if value:
        logger.debug("Popped retry hint for %s", agent_name)
    return value


async def apply_retry(
    state: dict[str, Any],
    exc: AgentOutputError,
    on_retry: Callable[..., Awaitable[None] | None] | None,
    *,
    persist_state: StateMutator | None = None,
) -> bool:
    if not is_tracked_agent(exc.agent_name):
        return False
    if max_retries_exceeded(state, exc.agent_name):
        logger.error(
            "Agent %s exceeded max retries (%s)",
            exc.agent_name,
            settings.AGENT_RETRY_ATTEMPTS,
        )
        return False

    def _prepare(target: dict[str, Any]) -> int:
        attempt = increment_retry_count(target, exc.agent_name)
        prepare_agent_retry(target, exc.agent_name)
        return attempt

    if persist_state is not None:
        attempt_ref: list[int] = [0]

        def _mutator(target: dict[str, Any]) -> None:
            attempt_ref[0] = _prepare(target)

        persist_state(_mutator)
        attempt = attempt_ref[0]
    else:
        attempt = _prepare(state)

    if on_retry:
        result = on_retry(exc.agent_name, attempt)
        if result is not None:
            await result
    return True

