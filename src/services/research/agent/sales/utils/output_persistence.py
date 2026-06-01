"""Persist PlanReAct FINAL_ANSWER text into session output_key when ADK omits it."""

from __future__ import annotations

import re
from typing import Any

from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG

from ......core.logging_config import logger

PLANNER_TAG_RE = re.compile(r"/\*[A-Z_]+\*/")

_FINAL_ANSWER_SPLIT_RE = re.compile(
    re.escape(FINAL_ANSWER_TAG) + r"\s*",
    re.IGNORECASE,
)


def extract_final_answer_payload(text: str) -> str | None:
    """Return content after /*FINAL_ANSWER*/ with planner tags stripped."""
    if not text or not text.strip():
        return None
    if FINAL_ANSWER_TAG.lower() not in text.lower():
        return None
    parts = _FINAL_ANSWER_SPLIT_RE.split(text, maxsplit=1)
    payload = parts[-1] if len(parts) > 1 else text
    payload = PLANNER_TAG_RE.sub("", payload).strip()
    return payload or None


def has_nonempty_output(state: dict[str, Any], output_key: str) -> bool:
    value = state.get(output_key)
    return value is not None and bool(str(value).strip())


def persist_output_key(
    state: dict[str, Any],
    *,
    agent_name: str,
    output_key: str,
    text: str,
) -> bool:
    """Write extracted FINAL_ANSWER (or full text fallback) into state. Returns True if set."""
    if has_nonempty_output(state, output_key):
        logger.debug(
            f"[Persist] Skipped agent={agent_name} output_key={output_key!r} "
            f"(already populated)"
        )
        return False
    payload = extract_final_answer_payload(text) or text.strip()
    if not payload:
        logger.warning(
            f"[Persist] Could not extract payload for agent={agent_name} "
            f"output_key={output_key!r} (missing or empty FINAL_ANSWER)"
        )
        return False
    state[output_key] = payload
    logger.info(
        f"[Persist] Stored output_key={output_key!r} for agent={agent_name} "
        f"({len(payload)} chars)"
    )
    return True


def collect_agent_visible_text(
    events: list[Any],
    *,
    agent_name: str,
    invocation_id: str | None = None,
) -> str:
    """Concatenate visible model text for an agent from session events (newest last)."""
    chunks: list[str] = []
    for event in events or []:
        if invocation_id and getattr(event, "invocation_id", None) != invocation_id:
            continue
        if getattr(event, "author", None) != agent_name:
            continue
        content = getattr(event, "content", None)
        if not content or not getattr(content, "parts", None):
            continue
        for part in content.parts:
            if getattr(part, "text", None) and not getattr(part, "thought", False):
                chunks.append(part.text)
    return "\n".join(chunks)


def persist_output_from_session_events(
    state: dict[str, Any],
    events: list[Any],
    *,
    agent_name: str,
    output_key: str,
    invocation_id: str | None = None,
) -> bool:
    """Scan events for FINAL_ANSWER text and persist to output_key if missing."""
    if has_nonempty_output(state, output_key):
        logger.debug(
            f"[Persist] Skipped event scan for agent={agent_name} "
            f"output_key={output_key!r} (already populated)"
        )
        return False
    combined = collect_agent_visible_text(
        events, agent_name=agent_name, invocation_id=invocation_id
    )
    if not combined.strip():
        logger.warning(
            f"[Persist] No visible events for agent={agent_name} "
            f"output_key={output_key!r} invocation_id={invocation_id}"
        )
        return False
    return persist_output_key(
        state,
        agent_name=agent_name,
        output_key=output_key,
        text=combined,
    )
