"""
ADK tools for the sales research agent.

- Google Search sub-agent (PlanReAct)
- BM25 draft verification
- Colt product catalog vector search
- Final report output guardrail validation (validate_final_report)
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG, REPLANNING_TAG
from google.adk.tools import google_search
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.google_search_agent_tool import GoogleSearchAgentTool
from google.adk.tools.tool_context import ToolContext

from ......core.config import settings
from ......core.exceptions import AgentOutputError
from ......core.logging_config import logger
from ......core.model import retry_config
from ......utils.guardrails import OutputGuardrail
from .....catalog.search import colt_product_search
from .evidence import aggregate_job_evidence, set_verification_state
from .verification import Bm25Verifier, EvidenceStore

# --- Constants -----------------------------------------------------------------

SEARCH_AGENT_NAME = "google_search_agent"
COLT_PRODUCT_SEARCH_TOOL = "colt_product_search"
VALIDATE_FINAL_REPORT_TOOL = "validate_final_report"

_verifier = Bm25Verifier()


# --- Google Search -------------------------------------------------------------


def make_search_agent_tool(
    *,
    description: str = "Searches the web and returns factual snippets, source URLs, and commentary.",
    instruction: str = (
        "You are a web search specialist. When given a request, use google_search "
        "and return factual snippets, source URLs, and relevant commentary from the results."
    ),
) -> GoogleSearchAgentTool:
    """Create a fresh GoogleSearchAgentTool wrapping a single-turn google_search LlmAgent."""
    search_agent = LlmAgent(
        name=SEARCH_AGENT_NAME,
        model=Gemini(model=settings.GEMINI_MODEL, retry_options=retry_config),
        description=description,
        instruction=instruction,
        tools=[google_search],
    )
    return GoogleSearchAgentTool(search_agent)


# --- BM25 draft verification -----------------------------------------------------


def verify_draft_answer(draft: str, tool_context: ToolContext) -> dict[str, Any]:
    """Verify draft against accumulated search evidence before /*FINAL_ANSWER*/."""
    agent_name: str = getattr(tool_context, "agent_name", "unknown")
    EvidenceStore(tool_context.state, agent_name=agent_name).ingest_grounding(
        None, agent_name=agent_name
    )
    session_id: str = "unknown"
    try:
        session = getattr(tool_context, "session", None)
        if session is not None:
            session_id = str(session.id)
    except Exception:
        pass
    result = _verifier.verify(
        draft, tool_context.state, agent_name=agent_name, session_id=session_id
    )

    set_verification_state(
        tool_context.state,
        agent_name,
        status=result.status,
        unsupported=result.unsupported,
    )

    if result.status == "PASSED":
        message = (
            f"Aggregated answer passed evidence check. Emit {FINAL_ANSWER_TAG} "
            "with the same verified aggregated answer only — no edits."
        )
    else:
        message = (
            f"Aggregated answer failed grounding check. Use {REPLANNING_TAG}, call "
            f"{SEARCH_AGENT_NAME} again to find missing evidence, revise the "
            f"aggregated answer under /*AGGREGATED_ANSWER*/, call verify_draft_answer "
            f"again, then emit {FINAL_ANSWER_TAG} only after PASSED."
        )
    return {
        "status": result.status,
        "unsupported": result.unsupported[:8],
        "message": message,
    }


verify_draft_answer_tool = FunctionTool(verify_draft_answer)


# --- Colt product catalog (re-exported from services.catalog.search) -----------

colt_product_search_tool = FunctionTool(colt_product_search)


# --- Final report validation ---------------------------------------------------


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


async def validate_final_report(draft: str, tool_context: ToolContext) -> dict[str, Any]:
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
