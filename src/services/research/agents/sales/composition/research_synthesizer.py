"""Research synthesizer agent: converts search evidence into per-domain output keys.

This agent bridges the gap between the unified QueryGeneratorAgent (which
produces search queries and executes searches) and the downstream synthesis
agents (AlignmentAnalyst, ReportCompiler) that expect structured per-domain
output keys like ``firmographicsagent_output``, ``geographicagent_output``, etc.
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmResponse

from ......core.config import settings
from ......core.logging_config import logger
from ...adk.callbacks import (
    after_agent_callback,
    after_model_callback,
    after_tool_callback,
    before_agent_callback,
    before_model_callback,
    before_tool_callback,
)
from ..callbacks.plan_react import (
    plan_after_agent,
    plan_after_model,
    plan_after_tool,
    plan_before_model,
    plan_before_tool,
)
from ..prompts.synthesis_research_prompts import (
    DOMAIN_OUTPUT_KEYS,
    RESEARCH_SYNTHESIZER_PROMPT,
)
from ..tools.search import make_search_agent_tool, verify_draft_answer_tool
from .leaf import create_plan_react_agent


def _strip_code_fence(text: str) -> str:
    """Return the contents of the first ``` fenced block, or *text* unchanged."""
    if "```json" in text:
        return text.split("```json", 1)[1].split("```", 1)[0]
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            return parts[1]
    return text


def _salvage_domain_values(json_text: str) -> dict[str, Any]:
    """Recover individual domain payloads from malformed/truncated JSON.

    The synthesizer emits one large object containing all 12 domains. A single
    unbalanced brace (or a response truncated at the output-token cap) makes
    ``json.loads`` fail for the *whole* object, which would otherwise discard
    11 perfectly good domains. Scan for each key and brace-match its value so
    every complete domain still survives.
    """
    salvaged: dict[str, Any] = {}
    for key in DOMAIN_OUTPUT_KEYS:
        marker = f'"{key}"'
        idx = json_text.find(marker)
        if idx < 0:
            continue
        colon = json_text.find(":", idx + len(marker))
        if colon < 0:
            continue
        rest = json_text[colon + 1 :].lstrip()
        if not rest:
            continue
        try:
            # raw_decode reads exactly one JSON value (object, array, or
            # scalar) starting at position 0 and ignores whatever follows,
            # so a broken sibling domain cannot take this one down with it.
            value, _ = json.JSONDecoder().raw_decode(rest)
        except ValueError:
            # Value itself is incomplete (truncated mid-domain) — skip it.
            continue
        salvaged[key] = value
    return salvaged


def _persist_domain_outputs_after_model(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    """Extract domain-level JSON outputs from FINAL_ANSWER and persist each key."""
    from google.adk.planners.plan_re_act_planner import FINAL_ANSWER_TAG

    content = llm_response.content
    if not content or not content.parts:
        return None

    text = "\n".join(
        (p.text or "").strip()
        for p in content.parts
        if getattr(p, "text", None) and not getattr(p, "thought", False)
    ).strip()

    if FINAL_ANSWER_TAG.lower() not in text.lower():
        return None

    # Extract JSON from after FINAL_ANSWER tag
    parts = text.lower().split(FINAL_ANSWER_TAG.lower(), 1)
    json_text = text[len(parts[0]) + len(FINAL_ANSWER_TAG) :].strip()
    json_text = _strip_code_fence(json_text)

    start = json_text.find("{")
    end = json_text.rfind("}") + 1
    body = json_text[start:end] if start >= 0 and end > start else json_text

    parsed: dict[str, Any] = {}
    try:
        candidate = json.loads(body)
        if isinstance(candidate, dict):
            parsed = candidate
        else:
            logger.error("[ResearchSynthesizer] FINAL_ANSWER is not a JSON object")
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"[ResearchSynthesizer] Failed to parse domain outputs JSON: {e}")

    if not parsed:
        parsed = _salvage_domain_values(json_text)
        if parsed:
            logger.warning(
                f"[ResearchSynthesizer] Salvaged {len(parsed)} domain key(s) from "
                "malformed FINAL_ANSWER JSON"
            )
        else:
            logger.error(
                "[ResearchSynthesizer] Could not recover any domain keys from "
                f"FINAL_ANSWER ({len(json_text)} chars)"
            )
            return None

    # Persist each domain output key into session state
    persisted: list[str] = []
    missing: list[str] = []
    for key in DOMAIN_OUTPUT_KEYS:
        value = parsed.get(key)
        if value is None:
            missing.append(key)
            continue

        # Convert dicts/lists to JSON strings for consistency with old agent outputs
        if isinstance(value, (dict, list)):
            state_value = json.dumps(value, ensure_ascii=False)
        else:
            state_value = str(value)

        if not state_value.strip():
            missing.append(key)
            continue

        callback_context.state[key] = state_value
        persisted.append(key)
        logger.info(
            f"[ResearchSynthesizer] Persisted {key} ({len(state_value)} chars)"
        )

    if missing:
        logger.warning(
            f"[ResearchSynthesizer] Missing domain keys in output: {', '.join(missing)}"
        )
    logger.info(
        f"[ResearchSynthesizer] Persisted {len(persisted)}/{len(DOMAIN_OUTPUT_KEYS)} "
        "domain output keys"
    )
    return None


def create_research_synthesizer(company_name: str = "Unknown"):
    """Create the research synthesizer agent.

    This agent takes the query plan from QueryGeneratorAgent, executes web
    searches, and synthesizes results into the 12 per-domain output keys
    that AlignmentAnalyst and ReportCompiler expect.
    """
    agent = create_plan_react_agent(
        name="ResearchSynthesizer",
        instruction=RESEARCH_SYNTHESIZER_PROMPT,
        output_key="research_synthesizer_output",
        description=(
            "Conducts web research based on the query plan and synthesizes "
            "findings into structured per-domain outputs for downstream agents."
        ),
        include_web_search=True,
        include_bm25_verify=True,
        model=settings.GEMINI_MODEL,
    )

    # Domain-output persistence must run FIRST. ADK stops walking the
    # after_model callback list at the first callback that returns a truthy
    # value, so anything appended after an observational callback that returns
    # the response is silently dead code.
    existing_after_model = agent.after_model_callback
    if isinstance(existing_after_model, list):
        agent.after_model_callback = [
            _persist_domain_outputs_after_model,
            *existing_after_model,
        ]
    elif existing_after_model is not None:
        agent.after_model_callback = [
            _persist_domain_outputs_after_model,
            existing_after_model,
        ]
    else:
        agent.after_model_callback = _persist_domain_outputs_after_model

    logger.info(
        f"Created ResearchSynthesizer for {company_name} "
        f"(domains={len(DOMAIN_OUTPUT_KEYS)})"
    )
    return agent
