"""Patch live ADK session state for pipeline retries."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...core.exceptions import AgentOutputError
from ...core.logging_config import logger
from .agent.utils.retry_errors import (
    RETRY_SCOPE_RUNNER_COLD,
    classify_error,
    retry_scope_for_error_class,
)

_MISSING_OUTPUT_PHRASE = "did not produce required output"
_MALFORMED_MARKERS = ("MALFORMED_FUNCTION_CALL", "Malformed function call")


def requires_cold_retry(exc: Exception) -> bool:
    """True when invocation resume cannot reliably re-run the failed leaf."""
    if isinstance(exc, AgentOutputError):
        error_class = getattr(exc, "error_class", None) or classify_error(str(exc))
        if error_class == "REPORT_VALIDATION_FAILED":
            logger.info(
                "Warm-retry decision: agent=%s error_class=%s reason=compiler_validation_failure",
                exc.agent_name,
                error_class,
            )
            return False
        if retry_scope_for_error_class(error_class) == RETRY_SCOPE_RUNNER_COLD:
            logger.info(
                "Cold-retry decision: agent=%s error_class=%s reason=error_class_scope",
                exc.agent_name,
                error_class,
            )
            return True
        msg = str(exc)
        if _MISSING_OUTPUT_PHRASE in msg:
            logger.info(
                "Cold-retry decision: agent=%s reason=missing_output_phrase",
                exc.agent_name,
            )
            return True
        if any(marker in msg for marker in _MALFORMED_MARKERS):
            logger.info(
                "Cold-retry decision: agent=%s reason=malformed_marker",
                exc.agent_name,
            )
            return True
    requires_cold = "contents are required" in str(exc).lower()
    if requires_cold:
        logger.info("Cold-retry decision: reason=contents_required_phrase")
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
                "Cannot persist session retry patch: unsupported session service %s",
                type(self._session_service).__name__,
            )
            return False

        stored = (
            self._session_service.sessions.get(self._app_name, {})
            .get(self._user_id, {})
            .get(self._session_id)
        )
        if stored is None:
            logger.warning(
                "Cannot persist session retry patch: session %s not found",
                self._session_id,
            )
            return False

        mutator(stored.state)
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
