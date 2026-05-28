"""Shared constants/helpers for ADK callback modules."""

from __future__ import annotations

from typing import Any

from opentelemetry import trace

_QUERY_INJECTION_PATTERNS = [
    "ignore previous",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "new instructions:",
    "system prompt",
    "jailbreak",
]

_SNIPPET_INJECTION_SIGNALS = [
    "ignore previous",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "new instructions:",
    "override your",
]


def contains_prompt_injection(
    text: str, *, extra_patterns: tuple[str, ...] = ()
) -> bool:
    low = text.lower()
    patterns = tuple(_QUERY_INJECTION_PATTERNS) + tuple(extra_patterns)
    return any(pattern in low for pattern in patterns)


def record_callback_span_event(
    event_name: str, attributes: dict[str, Any] | None = None
) -> None:
    """Attach lightweight callback events to the current span when available."""
    current_span = trace.get_current_span()
    if not current_span:
        return
    span_context = current_span.get_span_context()
    if not span_context or not span_context.is_valid:
        return
    current_span.add_event(event_name, attributes=attributes or {})

