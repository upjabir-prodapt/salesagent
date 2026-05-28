"""Helpers for mutating ADK session state (plain dict or State wrapper)."""

from __future__ import annotations

from typing import Any


def state_remove(state: Any, key: str) -> None:
    """Remove *key* from session state.

    ADK wraps live session state in ``sessions.state.State``, which supports
    ``get`` / ``__setitem__`` but not ``pop`` or ``__delitem__``.
    """
    if state is None:
        return
    if isinstance(state, dict):
        state.pop(key, None)
        return

    value = getattr(state, "_value", None)
    delta = getattr(state, "_delta", None)
    if isinstance(value, dict):
        value.pop(key, None)
    if isinstance(delta, dict):
        delta.pop(key, None)


def is_mutable_state(state: Any) -> bool:
    """True when *state* supports mapping-style get/set used by retry helpers."""
    return state is not None and hasattr(state, "get") and hasattr(state, "__setitem__")
