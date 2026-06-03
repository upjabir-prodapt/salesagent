"""Shared agent output validation used by ADK callbacks and runner retry."""

from __future__ import annotations

from typing import Any

from ....core.exceptions import AgentOutputError
from ....core.logging_config import logger
from .agent_contracts import get_output_key

__all__ = ["validate_agent_output"]


def validate_agent_output(state: dict[str, Any], agent_name: str) -> None:
    """Raise AgentOutputError if a tracked agent has no non-empty output in state."""
    output_key = get_output_key(agent_name)
    if not output_key:
        return

    if agent_name == "ReportCompiler":
        validation_status = str(state.get("report_validation_status") or "").upper()
        if validation_status and validation_status != "PASSED":
            phase_error = str(state.get("report_compiler_phase_error") or "").strip()
            detail = (
                phase_error or f"validate_final_report status was {validation_status!r}"
            )
            logger.warning(
                f"[Validation] agent={agent_name} keeping output despite "
                f"validation_status={validation_status!r} detail={detail}"
            )

    value = state.get(output_key)
    if value is None or not str(value).strip():
        logger.warning(
            f"[Validation] agent={agent_name} error_class=MISSING_OUTPUT "
            f"output_key={output_key!r}"
        )
        raise AgentOutputError(
            (
                f"{agent_name} completed but required output '{output_key}' remained empty "
                "in session state after model-response output persistence."
            ),
            agent_name=agent_name,
            output_key=output_key,
            error_class="MISSING_OUTPUT",
        )
