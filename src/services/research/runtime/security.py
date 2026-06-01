"""Shared prompt-injection detection for ADK callbacks."""

from __future__ import annotations

INJECTION_PATTERNS: tuple[str, ...] = (
    "ignore previous",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "new instructions:",
    "system prompt",
    "developer message",
    "jailbreak",
    "override your",
)


def has_injection(text: str) -> bool:
    """Return True when text contains a known injection pattern."""
    low = text.lower()
    return any(p in low for p in INJECTION_PATTERNS)
