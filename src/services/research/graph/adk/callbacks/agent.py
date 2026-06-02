"""Agent lifecycle callbacks (before/after agent)."""

from __future__ import annotations

from contextlib import suppress

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from ......core.logging_config import logger
from ....domain.agent_contracts import (
    get_output_key,
    is_tracked_agent,
    list_missing_research_outputs,
    validate_research_outputs_complete,
)
from ....domain.output_validation import validate_agent_output
from ....run.telemetry import track_agent_end, track_agent_start
from ...sales.tools.output_persistence import persist_output_from_session_events
from ...sales.tools.verification import Bm25Verifier
from .common import record_callback_span_event

__all__ = ["before_agent_callback", "after_agent_callback"]


async def before_agent_callback(callback_context: CallbackContext) -> None:
    """Called immediately before an agent executes."""
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id

    try:
        import asyncio
        import random

        parallel_researchers = {
            "FirmographicsGeographicAgent",
            "ExecutiveAgent",
            "StrategyComplianceAgent",
            "MarketEcosystemAgent",
            "TechStackAgent",
            "SignalsOrchestrator",
            "GrowthSignals",
            "RiskSignals",
            "CampaignSignals",
        }
        if agent_name in parallel_researchers:
            delay = random.uniform(1.0, 5.0)
            logger.debug(
                f"[Callback] Staggering {agent_name} start by {delay:.2f}s to protect quota"
            )
            await asyncio.sleep(delay)
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Staggering failed for {agent_name}: {e}")

    logger.info(
        f"[Callback] Before Agent starting: {agent_name}",
        extra={"agent": agent_name, "invocation": invocation_id},
    )
    record_callback_span_event(
        "adk.before_agent",
        {"agent_name": agent_name, "invocation_id": invocation_id},
    )

    try:
        track_agent_start(callback_context)
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Telemetry] track_agent_start failed for {agent_name}: {e}")

    try:
        callback_context.state["current_executing_agent"] = agent_name
        agent_status_map = dict(callback_context.state.get("agent_status_map") or {})
        agent_status_map[agent_name] = "running"
        callback_context.state["agent_status_map"] = agent_status_map
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Could not set current_executing_agent: {e}")

    if agent_name == "AlignmentAnalyst":
        missing = list_missing_research_outputs(callback_context.state)
        if missing:
            logger.warning(
                f"[Gate] Blocking AlignmentAnalyst: missing research outputs: "
                f"{', '.join(missing)}"
            )
        validate_research_outputs_complete(callback_context.state)

    if agent_name == "ReportCompiler":
        validate_agent_output(callback_context.state, "AlignmentAnalyst")

    return None


def after_agent_callback(callback_context: CallbackContext) -> types.Content | None:
    """Called after an agent completes execution."""
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id

    logger.info(
        f"[Callback] After Agent starting: {agent_name}",
        extra={"agent": agent_name, "invocation": invocation_id},
    )
    record_callback_span_event(
        "adk.after_agent",
        {"agent_name": agent_name, "invocation_id": invocation_id},
    )

    try:
        track_agent_end(callback_context)
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Telemetry] track_agent_end failed for {agent_name}: {e}")

    output_key = get_output_key(agent_name)
    if is_tracked_agent(agent_name) and output_key:
        persisted = persist_output_from_session_events(
            callback_context.state,
            callback_context.session.events,
            agent_name=agent_name,
            output_key=output_key,
            invocation_id=callback_context.invocation_id,
        )
        if not persisted and not callback_context.state.get(output_key):
            logger.warning(
                f"[Persist] after_agent persist failed for agent={agent_name} "
                f"output_key={output_key!r} invocation_id={invocation_id}"
            )

    try:
        agent_status_map = dict(callback_context.state.get("agent_status_map") or {})
        if is_tracked_agent(agent_name) and output_key:
            value = callback_context.state.get(output_key)
            if value is None or not str(value).strip():
                agent_status_map[agent_name] = "failed_missing_output"
                logger.warning(
                    f"[Callback] Agent {agent_name} ended without output_key="
                    f"{output_key!r} invocation_id={invocation_id}"
                )
            else:
                callback_context.state["last_completed_agent"] = agent_name
                agent_status_map[agent_name] = "completed"
        else:
            callback_context.state["last_completed_agent"] = agent_name
            agent_status_map[agent_name] = "completed"
        callback_context.state["agent_status_map"] = agent_status_map
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Could not update agent_status_map: {e}")

    if is_tracked_agent(agent_name):
        validate_agent_output(callback_context.state, agent_name)
        _record_bm25_telemetry(callback_context, agent_name)

    return None


def _record_bm25_telemetry(callback_context: CallbackContext, agent_name: str) -> None:
    """Optional post-agent BM25 telemetry (no retry)."""
    if agent_name in ("ReportCompiler", "AlignmentAnalyst"):
        return
    output_key = get_output_key(agent_name)
    if not output_key:
        return
    draft = callback_context.state.get(output_key)
    if not draft or not str(draft).strip():
        return
    session_id = "unknown"
    with suppress(Exception):
        session_id = str(
            getattr(callback_context, "session", None) and callback_context.session.id
        )
    try:
        result = Bm25Verifier().verify(
            str(draft),
            callback_context.state,
            agent_name=agent_name,
            session_id=session_id,
        )
        callback_context.state[f"{agent_name}_bm25_status"] = result.status
        callback_context.state[f"{agent_name}_bm25_unsupported"] = result.unsupported[
            :8
        ]
    except Exception as e:  # pragma: no cover
        logger.warning(
            f"[Callback] BM25 telemetry failed for agent={agent_name} "
            f"session_id={session_id}: {e}"
        )
