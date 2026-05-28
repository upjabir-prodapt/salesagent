"""Agent lifecycle callbacks (before/after agent)."""

from __future__ import annotations

from contextlib import suppress

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from .....core.logging_config import logger
from ..sales.utils.output_persistence import persist_output_from_session_events
from ..sales.utils.verification import Bm25Verifier
from .agent_pipeline import get_output_key, is_tracked_agent, validate_agent_output
from .callback_common import record_callback_span_event
from .telemetry import track_agent_end, track_agent_start


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

    if callback_context.agent_name == "ReportCompiler":
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
        persist_output_from_session_events(
            callback_context.state,
            callback_context.session.events,
            agent_name=agent_name,
            output_key=output_key,
            invocation_id=callback_context.invocation_id,
        )

    try:
        agent_status_map = dict(callback_context.state.get("agent_status_map") or {})
        if is_tracked_agent(agent_name) and output_key:
            value = callback_context.state.get(output_key)
            if value is None or not str(value).strip():
                agent_status_map[agent_name] = "failed_missing_output"
                logger.warning(
                    "[Callback] Agent %s ended without output_key %r",
                    agent_name,
                    output_key,
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

    if agent_name == "ReportCompiler":
        validation_status = callback_context.state.get("report_validation_status")
        if validation_status and validation_status != "PASSED":
            logger.warning(
                f"[ReportCompiler] Completed without PASSED validation "
                f"(status={validation_status!r}); clearing final_report"
            )
            callback_context.state["final_report"] = ""

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
        logger.debug(f"[Callback] BM25 telemetry failed for {agent_name}: {e}")

