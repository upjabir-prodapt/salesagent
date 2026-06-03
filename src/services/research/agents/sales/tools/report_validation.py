"""Final report validation tools for sales research agents."""

from __future__ import annotations

from typing import Any

from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG, REPLANNING_TAG
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

from ......core.config import settings
from ......core.exceptions import AgentOutputError
from ......core.logging_config import logger
from ......utils.guardrails import OutputGuardrail
from .evidence import aggregate_job_evidence

VALIDATE_FINAL_REPORT_TOOL = "validate_final_report"


def aggregate_raw_search_cache(state: dict[str, Any]) -> list[dict]:
    """Deprecated: use aggregate_job_evidence."""
    return aggregate_job_evidence(state)


def _state_to_dict(state: Any) -> dict[str, Any]:
    if hasattr(state, "to_dict"):
        return state.to_dict()
    return dict(state)


async def run_report_output_guardrail(
    draft: str,
    state: Any,
) -> tuple[str, list[dict[str, str]]]:
    """Run report OutputGuardrail and return status plus normalized violations."""
    state_dict = _state_to_dict(state)
    job_evidence = aggregate_job_evidence(state_dict)
    result = await OutputGuardrail().validate(
        draft, raw_search_cache=job_evidence or None
    )
    violations = [{"rule": v.rule, "detail": v.detail} for v in result.violations]
    status = "PASSED" if result.is_valid else "FAILED"
    return status, violations


def persist_report_validation_state(
    state: Any,
    *,
    status: str,
    violations: list[dict[str, str]],
    terminal: bool = False,
) -> None:
    """Persist report validation state keys consumed by callbacks/contracts."""
    state["report_validation_status"] = status
    state["report_validation_violations"] = violations
    if terminal:
        state["report_validation_terminal"] = True


async def ensure_report_validated(draft: str, state: Any) -> str:
    """Run fallback report validation when tool call was skipped."""
    current_status = str(state.get("report_validation_status") or "").upper()
    if current_status in ("PASSED", "FAILED"):
        return current_status

    status, violations = await run_report_output_guardrail(draft, state)
    persist_report_validation_state(
        state,
        status=status,
        violations=violations,
        terminal=status != "PASSED",
    )
    logger.info(
        f"[Validation] Fallback report validation completed: status={status} "
        f"violations={len(violations)}"
    )
    return status


async def validate_final_report(
    draft: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Run OutputGuardrail checks on a compiled markdown report draft."""
    attempts = int(tool_context.state.get("report_validation_attempts") or 0) + 1
    tool_context.state["report_validation_attempts"] = attempts

    max_attempts = settings.OUTPUT_GUARDRAIL_MAX_RETRIES + 1
    status, violations = await run_report_output_guardrail(draft, tool_context.state)
    logger.info(
        f"[Validation] Report validation result: status={status} "
        f"violations={len(violations)}"
    )
    persist_report_validation_state(
        tool_context.state,
        status=status,
        violations=violations,
    )

    if status == "PASSED":
        message = (
            f"Report passed all output guardrails. "
            f"Emit {FINAL_ANSWER_TAG} with this report only."
        )
        return {
            "status": status,
            "violations": violations[:10],
            "attempt": attempts,
            "max_attempts": max_attempts,
            "message": message,
        }

    details = "; ".join(f"{v['rule']}: {v['detail']}" for v in violations[:5])
    if attempts >= max_attempts:
        persist_report_validation_state(
            tool_context.state,
            status=status,
            violations=violations,
            terminal=True,
        )
        logger.error(
            f"[Validation] Report validation exhausted attempts={attempts}/"
            f"{max_attempts} violations={details}"
        )
        raise AgentOutputError(
            (
                f"Report failed validation (attempt {attempts}/{max_attempts}). "
                f"Maximum validation attempts exhausted. Do not emit {FINAL_ANSWER_TAG}. "
                f"Fail the run and mark the job as FAILED. Violations: {details}"
            ),
            agent_name="ReportCompiler",
            output_key="final_report",
            error_class="REPORT_VALIDATION_FAILED",
        )

    message = (
        f"Report failed validation (attempt {attempts}/{max_attempts}). "
        f"Use {REPLANNING_TAG}, fix the draft per violations, then call "
        f"{VALIDATE_FINAL_REPORT_TOOL} again. Violations: {details}"
    )
    logger.warning(
        f"[Validation] Report validation failed attempt={attempts}/{max_attempts} "
        f"violations={details}"
    )

    return {
        "status": status,
        "violations": violations[:10],
        "attempt": attempts,
        "max_attempts": max_attempts,
        "message": message,
    }


validate_final_report_tool = FunctionTool(validate_final_report)


__all__ = [
    "VALIDATE_FINAL_REPORT_TOOL",
    "OutputGuardrail",
    "aggregate_raw_search_cache",
    "run_report_output_guardrail",
    "persist_report_validation_state",
    "ensure_report_validated",
    "validate_final_report",
    "validate_final_report_tool",
]
