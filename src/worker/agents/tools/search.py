"""Search and verification tools for sales research agents."""

from __future__ import annotations

from typing import Any

from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG, REPLANNING_TAG
from google.adk.tools import google_search
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

from .evidence import set_verification_state
from .verification import Bm25Verifier, EvidenceStore

# --- Constants -----------------------------------------------------------------

SEARCH_TOOL_NAME = "google_search"

_verifier = Bm25Verifier()


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
            f"{SEARCH_TOOL_NAME} again to find missing evidence, revise the "
            f"aggregated answer under /*AGGREGATED_ANSWER*/, call verify_draft_answer "
            f"again, then emit {FINAL_ANSWER_TAG} only after PASSED."
        )
    return {
        "status": result.status,
        "unsupported": result.unsupported[:8],
        "message": message,
    }


verify_draft_answer_tool = FunctionTool(verify_draft_answer)


__all__ = [
    "SEARCH_TOOL_NAME",
    "google_search",
    "verify_draft_answer",
    "verify_draft_answer_tool",
]
