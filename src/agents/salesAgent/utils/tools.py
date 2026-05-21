"""
PlanReAct tools: Google Search sub-agent and BM25 draft verification.

- make_search_agent_tool: GoogleSearchAgentTool wrapper (ADK workaround for mixing tools)
- verify_draft_answer: FunctionTool scoring drafts against session evidence
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

from ....core.config import settings
from ....core.model import retry_config
from .verification import Bm25Verifier, EvidenceStore

SEARCH_AGENT_NAME = "google_search_agent"
COLT_PRODUCT_SEARCH_TOOL = "colt_product_search"

_verifier = Bm25Verifier()


def make_search_agent_tool(
    *,
    description: str = "Searches the web and returns factual snippets, source URLs, and commentary.",
    instruction: str = (
        "You are a web search specialist. When given a request, use google_search "
        "and return factual snippets, source URLs, and relevant commentary from the results."
    ),
) -> GoogleSearchAgentTool:
    """Create a fresh GoogleSearchAgentTool wrapping a single-turn google_search LlmAgent.

    Must be called fresh per agent instance to avoid ADK parent-conflict errors.
    The inner agent uses a plain Gemini model (no built-in GoogleSearch in
    generate_content_config) so that only the sub-agent calls the search API.
    """
    search_agent = LlmAgent(
        name=SEARCH_AGENT_NAME,
        model=Gemini(model=settings.GEMINI_MODEL, http_retry_options=retry_config),
        description=description,
        instruction=instruction,
        tools=[google_search],
    )
    return GoogleSearchAgentTool(search_agent)


def verify_draft_answer(draft: str, tool_context: ToolContext) -> dict[str, Any]:
    """Verify draft against accumulated search evidence before /*FINAL_ANSWER*/.

    Call this after /*REASONING*/ with the full draft text.
    Returns status PASSED or FAILED plus unsupported claims and a next-step message.
    """
    EvidenceStore(tool_context.state).ingest_grounding(None)
    agent_name: str = getattr(tool_context, "agent_name", "unknown")
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

    tool_context.state["verification_status"] = result.status
    tool_context.state["unsupported_claims"] = result.unsupported

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
