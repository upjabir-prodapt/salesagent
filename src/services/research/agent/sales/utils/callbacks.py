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

from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG, REPLANNING_TAG
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext

from ......core.logging_config import logger
from ...utils.agent_pipeline import get_output_key
from ...utils.callback_common import contains_prompt_injection
from .evidence import (
    evidence_key,
    get_unsupported_claims,
    get_verification_status,
    set_verification_state,
)
from .output_persistence import persist_output_key
from .tools import COLT_PRODUCT_SEARCH_TOOL, SEARCH_AGENT_NAME
from .verification import PLANNER_TAG_RE, EvidenceStore

# Minimum visible-answer length that triggers verification enforcement
MIN_FINAL_ANSWER_CHARS = 100

_PLAN_EXTRA_INJECTION_PATTERNS = ("developer message",)


def _has_injection(text: str) -> bool:
    return contains_prompt_injection(
        text, extra_patterns=_PLAN_EXTRA_INJECTION_PATTERNS
    )


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
    agent_name = callback_context.agent_name

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

    if get_verification_status(callback_context.state, agent_name) == "FAILED":
        bad = get_unsupported_claims(callback_context.state, agent_name)
        logger.warning(
            "verify_draft_answer FAILED for %s unsupported=%s",
            agent_name,
            bad[:3],
        )
        llm_request.append_instructions(
            [
                f"verify_draft_answer returned FAILED. Use {REPLANNING_TAG}, "
                f"call {SEARCH_AGENT_NAME} or {COLT_PRODUCT_SEARCH_TOOL} again to find "
                f"the missing evidence, revise the aggregated answer "
                f"(/*AGGREGATED_ANSWER*/), call verify_draft_answer again, "
                f"then emit {FINAL_ANSWER_TAG} only after PASSED with the same "
                f"verified aggregated answer. "
                f"Unsupported claims: {bad[:3]}"
            ]
        )
    return None


def plan_after_model(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    """Ingest grounding from the response; guard against unverified FINAL_ANSWER."""
    agent_name = callback_context.agent_name
    EvidenceStore(callback_context.state, agent_name=agent_name).ingest_grounding(
        None, llm_response=llm_response, agent_name=agent_name
    )

    text = _visible_text(llm_response)
    if len(text) >= MIN_FINAL_ANSWER_CHARS:
        output_key = get_output_key(agent_name)
        if output_key and FINAL_ANSWER_TAG.lower() in text.lower():
            persist_output_key(
                callback_context.state,
                agent_name=agent_name,
                output_key=output_key,
                text=text,
            )
        if get_verification_status(callback_context.state, agent_name) != "PASSED":
            set_verification_state(
                callback_context.state,
                agent_name,
                status="FAILED",
                unsupported=[
                    "FINAL_ANSWER was emitted before verify_draft_answer returned PASSED.",
                ],
            )
    return None


def plan_after_agent(callback_context: CallbackContext) -> None:
    """Ensure output_key is set from FINAL_ANSWER before generic after_agent validation."""
    agent_name = callback_context.agent_name
    output_key = get_output_key(agent_name)
    if not output_key:
        return None

    from .output_persistence import (
        has_nonempty_output,
        persist_output_from_session_events,
    )

    if has_nonempty_output(callback_context.state, output_key):
        return None

    persist_output_from_session_events(
        callback_context.state,
        callback_context.session.events,
        agent_name=agent_name,
        output_key=output_key,
        invocation_id=callback_context.invocation_id,
    )
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
    agent_name = getattr(tool_context, "agent_name", None) or "unknown"

    if tool.name in (SEARCH_AGENT_NAME, COLT_PRODUCT_SEARCH_TOOL):
        EvidenceStore(tool_context.state, agent_name=agent_name).append_search_response(
            tool_response,
            source_label=tool.name,
            agent_name=agent_name,
        )
        n = len(tool_context.state.get(evidence_key(agent_name), []))
        logger.info(f"{tool.name} done agent={agent_name} evidence_items={n}")
    elif tool.name == "verify_draft_answer":
        status = get_verification_status(tool_context.state, agent_name)
        logger.info(f"verify_draft_answer agent={agent_name} status={status}")
    return None
