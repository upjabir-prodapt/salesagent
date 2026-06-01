"""Patch live ADK session state for pipeline retries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.exceptions import AgentOutputError
from ....core.logging_config import logger
from .resilience.errors import RETRY_SCOPE_RUNNER_COLD, classify_error, retry_scope_for_error_class


def state_remove(state: Any, key: str) -> None:
    """Remove *key* from session state (dict or ADK State wrapper)."""
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


def requires_cold_retry(exc: Exception) -> bool:
    """True only when warm invocation resume is not viable."""
    if isinstance(exc, AgentOutputError):
        error_class = getattr(exc, "error_class", None) or classify_error(str(exc))
        if error_class == "REPORT_VALIDATION_FAILED":
            logger.info(
                f"[Retry] Warm-retry decision agent={exc.agent_name} "
                f"error_class={error_class} reason=compiler_validation_failure"
            )
            return False
        if retry_scope_for_error_class(error_class) == RETRY_SCOPE_RUNNER_COLD:
            logger.info(
                f"[Retry] Cold-retry decision agent={exc.agent_name} "
                f"error_class={error_class} reason=error_class_scope"
            )
            return True
        logger.info(
            f"[Retry] Warm-retry decision agent={exc.agent_name} "
            f"error_class={error_class}"
        )
        return False
    requires_cold = "contents are required" in str(exc).lower()
    if requires_cold:
        logger.info("[Retry] Cold-retry decision reason=contents_required_phrase")
    return requires_cold


class StoredSessionStateAdapter:
    """Encapsulates direct mutable access to ADK's in-memory session store."""

    def __init__(
        self,
        session_service: Any,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> None:
        self._session_service = session_service
        self._app_name = app_name
        self._user_id = user_id
        self._session_id = session_id

    def mutate(self, mutator: Callable[[dict[str, Any]], None]) -> bool:
        from google.adk.sessions.in_memory_session_service import InMemorySessionService

        if not isinstance(self._session_service, InMemorySessionService):
            logger.warning(
                f"[Persist] Cannot persist session retry patch: unsupported session "
                f"service {type(self._session_service).__name__}"
            )
            return False

        stored = (
            self._session_service.sessions.get(self._app_name, {})
            .get(self._user_id, {})
            .get(self._session_id)
        )
        if stored is None:
            logger.warning(
                f"[Persist] Cannot persist session retry patch: "
                f"session {self._session_id} not found"
            )
            return False

        mutator(stored.state)
        logger.debug(
            f"[Persist] Session retry patch applied session_id={self._session_id}"
        )
        return True


def mutate_stored_session_state(
    session_service: Any,
    *,
    app_name: str,
    user_id: str,
    session_id: str,
    mutator: Callable[[dict[str, Any]], None],
) -> bool:
    """Compatibility wrapper for mutating stored session.state."""
    return StoredSessionStateAdapter(
        session_service,
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
    ).mutate(mutator)
