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
from google.adk.planners.plan_re_act_planner import (
    ACTION_TAG,
    FINAL_ANSWER_TAG,
    PLANNING_TAG,
    REPLANNING_TAG,
)
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
from .tools import (
    COLT_PRODUCT_SEARCH_TOOL,
    SEARCH_AGENT_NAME,
    VALIDATE_FINAL_REPORT_TOOL,
)
from .verification import PLANNER_TAG_RE, EvidenceStore

# Minimum visible-answer length that triggers verification enforcement
MIN_FINAL_ANSWER_CHARS = 100
AGGREGATED_ANSWER_TAG = "/*AGGREGATED_ANSWER*/"

_PLAN_EXTRA_INJECTION_PATTERNS = ("developer message",)

ALIGNMENT_CATALOG_SEARCH_COUNT_KEY = "alignment_catalog_search_count"
ALIGNMENT_CATALOG_MISSING_FINAL_KEY = "alignment_catalog_missing_on_final"

REPORT_VALIDATION_TOOL_CALL_COUNT_KEY = "report_validation_tool_call_count"
REPORT_COMPILER_PHASES_KEY = "report_compiler_seen_planreact_phases"
REPORT_COMPILER_PHASE_ERROR_KEY = "report_compiler_phase_error"


def _raw_visible_text(llm_response: LlmResponse) -> str:
    """Return visible (non-thought) text including planner tags."""
    content = llm_response.content
    if not content or not content.parts:
        return ""
    return "\n".join(
        (p.text or "").strip()
        for p in content.parts
        if p.text and not getattr(p, "thought", False)
    ).strip()


def _record_report_compiler_phases(state: dict[str, Any], raw_text: str) -> set[str]:
    phases = set(state.get(REPORT_COMPILER_PHASES_KEY) or [])
    lowered = raw_text.lower()
    if PLANNING_TAG.lower() in lowered:
        phases.add(PLANNING_TAG)
    if AGGREGATED_ANSWER_TAG.lower() in lowered:
        phases.add(AGGREGATED_ANSWER_TAG)
    if ACTION_TAG.lower() in lowered:
        phases.add(ACTION_TAG)
    if FINAL_ANSWER_TAG.lower() in lowered:
        phases.add(FINAL_ANSWER_TAG)
    state[REPORT_COMPILER_PHASES_KEY] = sorted(phases)
    return phases


def _set_report_validation_failed(
    state: dict[str, Any],
    *,
    rule: str,
    detail: str,
) -> None:
    state["report_validation_status"] = "FAILED"
    violations = list(state.get("report_validation_violations") or [])
    violations.insert(0, {"rule": rule, "detail": detail})
    state["report_validation_violations"] = violations[:10]
    state[REPORT_COMPILER_PHASE_ERROR_KEY] = detail
    logger.warning(
        f"[Validation] ReportCompiler validation failed rule={rule} "
        f"detail={detail} violations={len(violations)}"
    )


def _has_injection(text: str) -> bool:
    return contains_prompt_injection(
        text, extra_patterns=_PLAN_EXTRA_INJECTION_PATTERNS
    )


def _visible_text(llm_response: LlmResponse) -> str:
    """Return the visible (non-thought) text from an LLM response, stripped of planner tags."""
    return PLANNER_TAG_RE.sub("", _raw_visible_text(llm_response)).strip()


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
            logger.warning(
                f"[Validation] Blocked injected input in plan_before_model "
                f"agent={agent_name}"
            )
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

    if agent_name != "ReportCompiler" and get_verification_status(
        callback_context.state, agent_name
    ) == "FAILED":
        bad = get_unsupported_claims(callback_context.state, agent_name)
        logger.warning(
            f"[Validation] verify_draft_answer FAILED for agent={agent_name} "
            f"unsupported={bad[:3]}"
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

    if agent_name == "AlignmentAnalyst":
        catalog_calls = int(
            callback_context.state.get(ALIGNMENT_CATALOG_SEARCH_COUNT_KEY) or 0
        )
        if catalog_calls <= 0:
            llm_request.append_instructions(
                [
                    "Before finalizing AlignmentAnalyst output, call "
                    f"{COLT_PRODUCT_SEARCH_TOOL}(query=...) for each mapped target "
                    "challenge so Colt solution claims are grounded in catalog evidence."
                ]
            )

    if agent_name == "ReportCompiler":
        validation_status = str(
            callback_context.state.get("report_validation_status") or ""
        ).upper()
        if validation_status != "PASSED":
            llm_request.append_instructions(
                [
                    f"Strict workflow required: emit {PLANNING_TAG} coverage checklist, "
                    f"then {AGGREGATED_ANSWER_TAG} full draft, then call "
                    f"{VALIDATE_FINAL_REPORT_TOOL}(draft=<full draft>), and emit "
                    f"{FINAL_ANSWER_TAG} only after report_validation_status is PASSED."
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

    raw_text = _raw_visible_text(llm_response)
    text = _visible_text(llm_response)
    has_final_answer_tag = FINAL_ANSWER_TAG.lower() in raw_text.lower()
    looks_like_report_markdown = "## " in text
    should_check = (
        len(text) >= MIN_FINAL_ANSWER_CHARS
        or has_final_answer_tag
        or (agent_name == "ReportCompiler" and looks_like_report_markdown)
    )
    if should_check:
        output_key = get_output_key(agent_name)
        if output_key and has_final_answer_tag:
            persist_output_key(
                callback_context.state,
                agent_name=agent_name,
                output_key=output_key,
                text=raw_text,
            )

        if agent_name == "AlignmentAnalyst" and has_final_answer_tag:
            catalog_calls = int(
                callback_context.state.get(ALIGNMENT_CATALOG_SEARCH_COUNT_KEY) or 0
            )
            if catalog_calls <= 0:
                callback_context.state[ALIGNMENT_CATALOG_MISSING_FINAL_KEY] = True
                logger.warning(
                    f"[Validation] AlignmentAnalyst emitted FINAL_ANSWER without "
                    f"{COLT_PRODUCT_SEARCH_TOOL} call"
                )

        if agent_name == "ReportCompiler":
            phases = _record_report_compiler_phases(callback_context.state, raw_text)
            validation_status = str(
                callback_context.state.get("report_validation_status") or ""
            ).upper()
            has_planner_tags = any(
                tag.lower() in raw_text.lower()
                for tag in (
                    PLANNING_TAG,
                    AGGREGATED_ANSWER_TAG,
                    ACTION_TAG,
                    FINAL_ANSWER_TAG,
                    REPLANNING_TAG,
                )
            )

            if "## " in text and not has_planner_tags:
                _set_report_validation_failed(
                    callback_context.state,
                    rule="output:missing_planreact_phase",
                    detail=(
                        "ReportCompiler emitted markdown without PlanReAct tags "
                        f"({PLANNING_TAG}, {AGGREGATED_ANSWER_TAG}, {ACTION_TAG}, "
                        f"{FINAL_ANSWER_TAG})."
                    ),
                )

            if has_final_answer_tag:
                if PLANNING_TAG not in phases or AGGREGATED_ANSWER_TAG not in phases:
                    _set_report_validation_failed(
                        callback_context.state,
                        rule="output:missing_planreact_phase",
                        detail=(
                            "ReportCompiler emitted FINAL_ANSWER before showing required "
                            f"phases ({PLANNING_TAG} and {AGGREGATED_ANSWER_TAG})."
                        ),
                    )
                elif validation_status != "PASSED":
                    _set_report_validation_failed(
                        callback_context.state,
                        rule="output:validation_not_passed",
                        detail=(
                            "ReportCompiler emitted FINAL_ANSWER before "
                            f"{VALIDATE_FINAL_REPORT_TOOL} reached PASSED."
                        ),
                    )
        elif has_final_answer_tag and get_verification_status(
            callback_context.state, agent_name
        ) != "PASSED":
            logger.warning(
                f"[Validation] agent={agent_name} emitted FINAL_ANSWER before "
                f"verify_draft_answer PASSED"
            )
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

    logger.info(
        f"[Persist] Fallback event scan for agent={agent_name} "
        f"output_key={output_key!r} invocation_id={callback_context.invocation_id}"
    )
    persisted = persist_output_from_session_events(
        callback_context.state,
        callback_context.session.events,
        agent_name=agent_name,
        output_key=output_key,
        invocation_id=callback_context.invocation_id,
    )
    if not persisted:
        logger.warning(
            f"[Persist] Fallback persist failed for agent={agent_name} "
            f"output_key={output_key!r}"
        )
    return None


def plan_before_tool(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    """Block injected content in search/catalog tool arguments."""
    if tool.name == SEARCH_AGENT_NAME and _has_injection(str(args.get("request", ""))):
        logger.warning(
            f"[Validation] Blocked injected search request "
            f"agent={getattr(tool_context, 'agent_name', 'unknown')}"
        )
        return {"error": "Search request blocked by input policy"}
    if tool.name == COLT_PRODUCT_SEARCH_TOOL and _has_injection(
        str(args.get("query", ""))
    ):
        logger.warning(
            f"[Validation] Blocked injected catalog query "
            f"agent={getattr(tool_context, 'agent_name', 'unknown')}"
        )
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
        if agent_name == "AlignmentAnalyst" and tool.name == COLT_PRODUCT_SEARCH_TOOL:
            tool_context.state[ALIGNMENT_CATALOG_SEARCH_COUNT_KEY] = int(
                tool_context.state.get(ALIGNMENT_CATALOG_SEARCH_COUNT_KEY) or 0
            ) + 1
    elif tool.name == "verify_draft_answer":
        status = get_verification_status(tool_context.state, agent_name)
        logger.info(f"[Validation] verify_draft_answer agent={agent_name} status={status}")
    elif tool.name == VALIDATE_FINAL_REPORT_TOOL and agent_name == "ReportCompiler":
        tool_context.state[REPORT_VALIDATION_TOOL_CALL_COUNT_KEY] = int(
            tool_context.state.get(REPORT_VALIDATION_TOOL_CALL_COUNT_KEY) or 0
        ) + 1
        status = str(tool_context.state.get("report_validation_status") or "UNKNOWN")
        violations = tool_context.state.get("report_validation_violations") or []
        logger.info(
            f"[Validation] validate_final_report agent={agent_name} status={status} "
            f"violations={len(violations)}"
        )
    return None
