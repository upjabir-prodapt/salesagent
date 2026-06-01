"""Retry error classification helpers."""

from __future__ import annotations

from typing import Any

from .....core.exceptions import AgentOutputError
from .....core.logging_config import logger
from ...domain.agent_contracts import (
    get_output_key,
    list_missing_research_outputs,
)

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
    "MISSING_OUTPUT": RETRY_SCOPE_LEAF_LOCAL,
    "MALFORMED_FUNCTION_CALL": RETRY_SCOPE_LEAF_LOCAL,
    "CONNECT_ERROR": RETRY_SCOPE_LEAF_LOCAL,
    "REPORT_VALIDATION_FAILED": RETRY_SCOPE_NONE,
    "RESOURCE_EXHAUSTED": RETRY_SCOPE_RUNNER_WARM,
    "AGENT_ERROR": RETRY_SCOPE_RUNNER_WARM,
    "MODEL_ERROR": RETRY_SCOPE_RUNNER_WARM,
}

__all__ = [
    "RETRY_SCOPE_LEAF_LOCAL",
    "RETRY_SCOPE_NONE",
    "RETRY_SCOPE_RUNNER_COLD",
    "RETRY_SCOPE_RUNNER_WARM",
    "agent_failure_from_event",
    "classify_error",
    "is_leaf_retryable_exception",
    "resolve_retry_agents",
    "retry_scope_for_error_class",
]


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
        logger.debug(
            f"[Retry] Leaf retry skipped: nested AgentOutputError ({exc.error_class})"
        )
        return False
    detail = str(exc).lower()
    for marker in _NO_RETRY_MARKERS:
        if marker in detail:
            logger.debug(
                f"[Retry] Leaf retry skipped: matched no-retry marker {marker!r}"
            )
            return False
    return True


def resolve_retry_agents(exc: AgentOutputError, state: dict[str, Any]) -> list[str]:
    """Agents to reset and re-run for this failure (failed agent(s) only)."""
    agent_name = exc.agent_name
    if agent_name == "AlignmentAnalyst":
        missing = list_missing_research_outputs(state)
        if missing:
            logger.info(
                f"[Retry] Alignment blocked: retrying missing research agents only: "
                f"{missing}"
            )
            return missing
        return ["AlignmentAnalyst"]
    if agent_name == "ReportCompiler":
        return ["ReportCompiler"]
    return [agent_name]


def agent_failure_from_event(author: str, detail: str) -> AgentOutputError:
    output_key = get_output_key(author) or f"{author.lower()}_output"
    error_class = classify_error(detail)
    logger.warning(
        f"[Retry] ADK event failure agent={author} error_class={error_class} "
        f"output_key={output_key!r} detail={detail}"
    )
    return AgentOutputError(
        f"Agent '{author}' failed: {detail}",
        agent_name=author,
        output_key=output_key,
        error_class=error_class,
    )
