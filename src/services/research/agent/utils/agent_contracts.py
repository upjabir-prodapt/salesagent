"""Stable agent output-key contracts and validators."""

from __future__ import annotations

from typing import Any

from .....core.exceptions import AgentOutputError
from ...domain.agent_contracts import (
    AGENT_OUTPUT_KEYS,
    get_output_key,
    is_tracked_agent,
)

__all__ = [
    "AGENT_OUTPUT_KEYS",
    "get_output_key",
    "is_tracked_agent",
    "validate_agent_output",
]


def validate_agent_output(state: dict[str, Any], agent_name: str) -> None:
    """Raise AgentOutputError if a tracked agent has no non-empty output in state."""
    output_key = get_output_key(agent_name)
    if not output_key:
        return

    if agent_name == "ReportCompiler":
        validation_status = str(state.get("report_validation_status") or "").upper()
        if validation_status and validation_status != "PASSED":
            raise AgentOutputError(
                (
                    "ReportCompiler output blocked because ReportVerificationAgent "
                    f"status was {validation_status!r}. Re-run ReportCompiler and "
                    "keep FINAL_ANSWER gated on PASSED validation."
                ),
                agent_name=agent_name,
                output_key=output_key,
                error_class="REPORT_VALIDATION_FAILED",
            )

    value = state.get(output_key)
    if value is None or not str(value).strip():
        raise AgentOutputError(
            (
                f"{agent_name} completed but required output '{output_key}' remained empty "
                "in session state after model-response output persistence."
            ),
            agent_name=agent_name,
            output_key=output_key,
            error_class="MISSING_OUTPUT",
        )

