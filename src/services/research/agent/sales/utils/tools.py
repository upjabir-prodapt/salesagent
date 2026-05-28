"""
ADK tools for the sales research agent.

- Google Search sub-agent (PlanReAct)
- BM25 draft verification
- Colt product catalog vector search
- Final report output guardrail validation (ReportVerificationAgent)
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG, REPLANNING_TAG
from google.adk.tools import google_search
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.google_search_agent_tool import GoogleSearchAgentTool
from google.adk.tools.tool_context import ToolContext

from ......core.config import settings
from ......core.model import retry_config
from ......utils.guardrails import OutputGuardrail
from .....catalog.search import colt_product_search
from .evidence import aggregate_job_evidence, set_verification_state
from .verification import Bm25Verifier, EvidenceStore

# --- Constants -----------------------------------------------------------------

SEARCH_AGENT_NAME = "google_search_agent"
COLT_PRODUCT_SEARCH_TOOL = "colt_product_search"
REPORT_VERIFICATION_AGENT_NAME = "ReportVerificationAgent"

REPORT_VERIFICATION_INSTRUCTION = """
You validate compiled sales research markdown reports.

When you receive a request containing a report draft:
1. Call `validate_final_report` with the **full draft markdown** exactly as provided in the request.
2. Return the tool JSON result only — no extra commentary, no new facts, no rewriting of the report.
"""

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
            f"Draft passed evidence check. Emit {FINAL_ANSWER_TAG} with this draft only."
        )
    else:
        message = (
            f"Draft failed grounding check. Use {REPLANNING_TAG}, call "
            f"{SEARCH_AGENT_NAME} again to find missing evidence, revise the draft, "
            "then call verify_draft_answer again before emitting FINAL_ANSWER."
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


async def validate_final_report(draft: str, tool_context: ToolContext) -> dict[str, Any]:
    """Run OutputGuardrail checks on a compiled markdown report draft."""
    attempts = int(tool_context.state.get("report_validation_attempts") or 0) + 1
    tool_context.state["report_validation_attempts"] = attempts

    max_attempts = settings.OUTPUT_GUARDRAIL_MAX_RETRIES + 1
    state_dict = (
        tool_context.state.to_dict()
        if hasattr(tool_context.state, "to_dict")
        else dict(tool_context.state)
    )
    job_evidence = aggregate_job_evidence(state_dict)

    result = await OutputGuardrail().validate(
        draft, raw_search_cache=job_evidence or None
    )

    violations = [{"rule": v.rule, "detail": v.detail} for v in result.violations]
    status = "PASSED" if result.is_valid else "FAILED"

    tool_context.state["report_validation_status"] = status
    tool_context.state["report_validation_violations"] = violations

    if status == "PASSED":
        message = (
            f"Report passed all output guardrails. "
            f"Emit {FINAL_ANSWER_TAG} with this report only."
        )
    elif attempts >= max_attempts:
        message = (
            f"Report failed validation (attempt {attempts}/{max_attempts}). "
            "Maximum validation attempts reached — fix what you can and emit "
            f"{FINAL_ANSWER_TAG} only if you cannot resolve remaining issues."
        )
    else:
        details = "; ".join(f"{v['rule']}: {v['detail']}" for v in violations[:5])
        message = (
            f"Report failed validation (attempt {attempts}/{max_attempts}). "
            f"Use {REPLANNING_TAG}, fix the draft per violations, then call "
            f"{REPORT_VERIFICATION_AGENT_NAME} again. Violations: {details}"
        )

    return {
        "status": status,
        "violations": violations[:10],
        "attempt": attempts,
        "max_attempts": max_attempts,
        "message": message,
    }


validate_final_report_tool = FunctionTool(validate_final_report)


def create_report_verification_agent() -> LlmAgent:
    """Create a fresh ReportVerificationAgent (one per ReportCompiler instance)."""
    return LlmAgent(
        name=REPORT_VERIFICATION_AGENT_NAME,
        model=Gemini(model=settings.GEMINI_MODEL, retry_options=retry_config),
        description=(
            "Validates the final markdown report against format, completeness, "
            "prohibited content, and evidence-backed hallucination checks."
        ),
        instruction=REPORT_VERIFICATION_INSTRUCTION,
        tools=[validate_final_report_tool],
    )


def make_report_verification_agent_tool() -> AgentTool:
    """AgentTool wrapping ReportVerificationAgent for ReportCompiler PlanReAct loop."""
    return AgentTool(
        create_report_verification_agent(),
        skip_summarization=True,
        include_plugins=False,
    )
