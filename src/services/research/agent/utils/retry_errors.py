"""Retry error classification helpers."""

from __future__ import annotations

from .....core.exceptions import AgentOutputError
from .agent_contracts import get_output_key

_MALFORMED_MARKERS = ("MALFORMED_FUNCTION_CALL", "Malformed function call")
_NO_RETRY_MARKERS = (
    "input validation",
    "blocked by input guardrails",
    "prompt injection",
    "safety block",
    "output validation",
)

RETRY_SCOPE_LEAF_LOCAL = "LEAF_LOCAL"
RETRY_SCOPE_RUNNER_COLD = "RUNNER_COLD"
RETRY_SCOPE_RUNNER_WARM = "RUNNER_WARM"
RETRY_SCOPE_NONE = "NO_RETRY"

_ERROR_CLASS_TO_SCOPE = {
    "MISSING_OUTPUT": RETRY_SCOPE_RUNNER_COLD,
    "MALFORMED_FUNCTION_CALL": RETRY_SCOPE_RUNNER_COLD,
    "CONNECT_ERROR": RETRY_SCOPE_RUNNER_COLD,
    "REPORT_VALIDATION_FAILED": RETRY_SCOPE_NONE,
    "RESOURCE_EXHAUSTED": RETRY_SCOPE_RUNNER_WARM,
    "AGENT_ERROR": RETRY_SCOPE_RUNNER_WARM,
    "MODEL_ERROR": RETRY_SCOPE_RUNNER_WARM,
}


def classify_error(detail: str) -> str:
    if any(marker in detail for marker in _MALFORMED_MARKERS):
        return "MALFORMED_FUNCTION_CALL"
    low = detail.lower()
    if "connect" in low or "connection" in low or "tls" in low:
        return "CONNECT_ERROR"
    if "resource_exhausted" in low or "429" in detail or "quota" in low:
        return "RESOURCE_EXHAUSTED"
    return "AGENT_ERROR"


def retry_scope_for_error_class(error_class: str | None) -> str:
    if not error_class:
        return RETRY_SCOPE_RUNNER_WARM
    return _ERROR_CLASS_TO_SCOPE.get(error_class, RETRY_SCOPE_RUNNER_WARM)


def is_leaf_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, AgentOutputError):
        return False
    detail = str(exc).lower()
    if any(marker in detail for marker in _NO_RETRY_MARKERS):
        return False
    return "did not produce required output" not in detail


def agent_failure_from_event(author: str, detail: str) -> AgentOutputError:
    output_key = get_output_key(author) or f"{author.lower()}_output"
    return AgentOutputError(
        f"Agent '{author}' failed: {detail}",
        agent_name=author,
        output_key=output_key,
        error_class=classify_error(detail),
    )

