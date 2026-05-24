"""Pipeline output validation and per-agent retry helpers for SalesResearchAgent."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from google.adk.agents.run_config import RunConfig
from google.adk.runners import Runner
from google.genai import types

from .....core.config import settings
from .....core.exceptions import AgentOutputError, ServiceError
from .....core.logging_config import logger

# Leaf LlmAgents that must write session.state[output_key] on success.
AGENT_OUTPUT_KEYS: dict[str, str] = {
    "FirmographicsAgent": "firmographicsagent_output",
    "GeographicAgent": "geographicagent_output",
    "ExecutiveAgent": "executiveagent_output",
    "StrategyAgent": "strategyagent_output",
    "ComplianceAgent": "complianceagent_output",
    "MarketAgent": "marketagent_output",
    "EcosystemAgent": "ecosystemagent_output",
    "TechStackAgent": "techstackagent_output",
    "ProcurementAgent": "procurementagent_output",
    "GrowthSignals": "growthsignals_output",
    "RiskSignals": "risksignals_output",
    "CampaignSignals": "campaignsignals_output",
    "AlignmentAnalyst": "alignment_output",
    "ReportCompiler": "final_report",
}

AGENT_RETRY_COUNTS_KEY = "agent_retry_counts"
PIPELINE_RETRY_AGENT_KEY = "pipeline_retry_agent"

EventHandler = Callable[[Any], Awaitable[None] | None]


def get_output_key(agent_name: str) -> str | None:
    return AGENT_OUTPUT_KEYS.get(agent_name)


def is_tracked_agent(agent_name: str) -> bool:
    return agent_name in AGENT_OUTPUT_KEYS


def validate_agent_output(state: dict[str, Any], agent_name: str) -> None:
    """Raise AgentOutputError if a tracked agent has no non-empty output in state."""
    output_key = get_output_key(agent_name)
    if not output_key:
        return
    value = state.get(output_key)
    if value is None or not str(value).strip():
        raise AgentOutputError(
            f"{agent_name} did not produce required output '{output_key}' in session state.",
            agent_name=agent_name,
            output_key=output_key,
        )


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
    """True when this agent has used all allowed attempts (matches prior tenacity total)."""
    if settings.AGENT_RETRY_ATTEMPTS <= 1:
        return get_retry_count(state, agent_name) >= 1
    return get_retry_count(state, agent_name) >= settings.AGENT_RETRY_ATTEMPTS - 1


def prepare_agent_retry(state: dict[str, Any], agent_name: str) -> str | None:
    """Clear failed agent output and mark status for an ADK resume retry."""
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

    attempt = get_retry_count(state, agent_name)
    logger.warning(
        "Preparing retry for %s (attempt %s/%s), cleared output key %r",
        agent_name,
        attempt,
        settings.AGENT_RETRY_ATTEMPTS,
        output_key,
    )
    return output_key


def agent_failure_from_event(author: str, detail: str) -> AgentOutputError:
    """Build AgentOutputError for ADK error events from a tracked agent."""
    output_key = get_output_key(author) or f"{author.lower()}_output"
    return AgentOutputError(
        f"Agent '{author}' failed: {detail}",
        agent_name=author,
        output_key=output_key,
    )


async def _apply_retry(
    state: dict[str, Any],
    exc: AgentOutputError,
    on_retry: Callable[[str, int], Awaitable[None] | None] | None,
) -> bool:
    """Increment retry counters, clear output, invoke optional hook. Returns True if retry allowed."""
    if not is_tracked_agent(exc.agent_name):
        return False
    if max_retries_exceeded(state, exc.agent_name):
        logger.error(
            "Agent %s exceeded max retries (%s)",
            exc.agent_name,
            settings.AGENT_RETRY_ATTEMPTS,
        )
        return False
    attempt = increment_retry_count(state, exc.agent_name)
    prepare_agent_retry(state, exc.agent_name)
    if on_retry:
        result = on_retry(exc.agent_name, attempt)
        if result is not None:
            await result
    return True


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
    on_retry: Callable[[str, int], Awaitable[None] | None] | None = None,
) -> None:
    """Run ADK runner; on tracked agent failure, resume and retry that step up to AGENT_RETRY_ATTEMPTS."""
    last_invocation_id: str | None = None
    initial_message = new_message

    while True:
        run_kwargs: dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "run_config": run_config,
        }
        if last_invocation_id:
            logger.info("Resuming ADK invocation %s after agent failure", last_invocation_id)
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
            state = await get_session_state()
            if not await _apply_retry(state, exc, on_retry):
                raise
            # Only use the initial user message on the first runner entry.
            initial_message = None
