"""Helpers to reset ADK agent completion state for warm invocation resume."""

from __future__ import annotations

from typing import Any

from google.adk.agents.base_agent import BaseAgentState
from google.adk.events import Event
from google.adk.events.event_actions import EventActions

from .....core.logging_config import logger

__all__ = ["append_agent_reset_events"]


def append_agent_reset_events(
    session: Any,
    agent_names: list[str],
    *,
    invocation_id: str,
    branch: str | None = None,
) -> None:
    """Append agent-state events so resume treats agents as not finished."""
    events = getattr(session, "events", None)
    if events is None:
        logger.warning(
            "[Retry] Cannot append agent reset events: session has no events list"
        )
        return
    empty_state = BaseAgentState().model_dump(mode="json")
    for agent_name in agent_names:
        events.append(
            Event(
                invocation_id=invocation_id,
                author=agent_name,
                branch=branch,
                actions=EventActions(agent_state=empty_state),
            )
        )
        logger.info(
            f"[Retry] Appended ADK agent-state reset event for agent={agent_name} "
            f"invocation_id={invocation_id}"
        )
