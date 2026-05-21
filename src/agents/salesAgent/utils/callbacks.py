"""
PlanReAct-specific callbacks for sales research agents.

These callbacks are layered on top of the generic ones in src/utils/callbacks.py.
They handle:
  - Input injection guard (before_model, before_tool)
  - Grounding metadata ingestion from google_search_agent responses (after_model, after_tool)
  - Injecting REPLANNING hints when verify_draft_answer previously returned FAILED
  - Flagging a missing verification step before FINAL_ANSWER
"""

from __future__ import annotations

import re
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG, REPLANNING_TAG
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ....core.logging_config import logger
from .tools import COLT_PRODUCT_SEARCH_TOOL, SEARCH_AGENT_NAME
from .verification import EvidenceStore, PLANNER_TAG_RE

# Minimum visible-answer length that triggers verification enforcement
MIN_FINAL_ANSWER_CHARS = 100

QUERY_INJECTION_PATTERNS = (
    "ignore previous",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "new instructions:",
    "system prompt",
    "developer message",
    "jailbreak",
)


def _has_injection(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in QUERY_INJECTION_PATTERNS)


def _visible_text(llm_response: LlmResponse) -> str:
    """Return the visible (non-thought) text from an LLM response, stripped of planner tags."""
    content = llm_response.content
    if not content or not content.parts:
        return ""
    return PLANNER_TAG_RE.sub(
        "",
        "\n".join(
            (p.text or "").strip()
            for p in content.parts
            if p.text and not getattr(p, "thought", False)
        ),
    ).strip()


# --- Callback functions -------------------------------------------------------


def plan_before_model(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    """Injection guard + FAILED-verification replan hint injected into next request."""
    # Scan latest user turn for prompt injection
    for content in reversed(llm_request.contents or []):
        if getattr(content, "role", None) != "user":
            continue
        message = " ".join(
            p.text for p in (content.parts or []) if getattr(p, "text", None)
        )
        if _has_injection(message):
            from google.genai import types

            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            text="Blocked potentially injected input in user request."
                        )
                    ],
                )
            )
        break

    # Remind the model to replan if the last verify call failed
    if callback_context.state.get("verification_status") == "FAILED":
        bad = callback_context.state.get("unsupported_claims", [])
        logger.warning(
            "verify_draft_answer FAILED for %s unsupported=%s",
            callback_context.agent_name,
            bad[:3],
        )
        llm_request.append_instructions(
            [
                f"verify_draft_answer returned FAILED. Use {REPLANNING_TAG}, "
                f"call {SEARCH_AGENT_NAME} or {COLT_PRODUCT_SEARCH_TOOL} again to find "
                f"the missing evidence, revise the draft, call verify_draft_answer again, "
                f"then emit {FINAL_ANSWER_TAG} only after PASSED. "
                f"Unsupported claims: {bad[:3]}"
            ]
        )
    return None


def plan_after_model(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    """Ingest grounding from the response; guard against unverified FINAL_ANSWER."""
    EvidenceStore(callback_context.state).ingest_grounding(
        None, llm_response=llm_response
    )

    text = _visible_text(llm_response)
    if len(text) >= MIN_FINAL_ANSWER_CHARS:
        if callback_context.state.get("verification_status") != "PASSED":
            callback_context.state["verification_status"] = "FAILED"
            callback_context.state["unsupported_claims"] = [
                "FINAL_ANSWER was emitted before verify_draft_answer returned PASSED.",
            ]
    return None


def plan_before_tool(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    """Block injected content in search/catalog tool arguments."""
    if tool.name == SEARCH_AGENT_NAME and _has_injection(str(args.get("request", ""))):
        return {"error": "Search request blocked by input policy"}
    if tool.name == COLT_PRODUCT_SEARCH_TOOL and _has_injection(
        str(args.get("query", ""))
    ):
        return {"error": "Catalog search blocked by input policy"}
    return None


def plan_after_tool(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: Any,
) -> dict[str, Any] | None:
    """Ingest search/catalog tool responses into the EvidenceStore."""
    if tool.name in (SEARCH_AGENT_NAME, COLT_PRODUCT_SEARCH_TOOL):
        EvidenceStore(tool_context.state).append_search_response(
            tool_response, source_label=tool.name
        )
        n = len(tool_context.state.get("search_evidence", []))
        agent = getattr(tool_context, "agent_name", "?")
        logger.info("%s done agent=%s evidence_items=%s", tool.name, agent, n)
    elif tool.name == "verify_draft_answer":
        logger.info(
            "verify_draft_answer status=%s",
            tool_context.state.get("verification_status"),
        )
    return None
