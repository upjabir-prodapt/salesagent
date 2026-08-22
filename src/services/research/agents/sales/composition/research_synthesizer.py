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

    # Try to parse the JSON
    parsed: dict[str, Any] | None = None
    try:
        # Handle ```json fenced blocks
        if "```json" in json_text:
            json_text = json_text.split("```json")[1].split("```")[0]
        elif "```" in json_text:
            json_text = json_text.split("```")[1].split("```")[0]

        # Find first { and last }
        start = json_text.find("{")
        end = json_text.rfind("}") + 1
        if start >= 0 and end > start:
            json_text = json_text[start:end]

        parsed = json.loads(json_text)
    except (json.JSONDecodeError, IndexError, ValueError) as e:
        logger.error(
            f"[ResearchSynthesizer] Failed to parse domain outputs JSON: {e}"
        )
        return None

    if not isinstance(parsed, dict):
        logger.error(
            "[ResearchSynthesizer] FINAL_ANSWER is not a JSON object"
        )
        return None

    # Persist each domain output key into session state
    persisted_count = 0
    for key in DOMAIN_OUTPUT_KEYS:
        value = parsed.get(key)
        if value is None:
            logger.warning(
                f"[ResearchSynthesizer] Missing domain key in output: {key}"
            )
            continue

        # Convert dicts/lists to JSON strings for consistency with old agent outputs
        if isinstance(value, (dict, list)):
            state_value = json.dumps(value, ensure_ascii=False)
        else:
            state_value = str(value)

        if state_value.strip():
            callback_context.state[key] = state_value
            persisted_count += 1
            logger.info(
                f"[ResearchSynthesizer] Persisted {key} "
                f"({len(state_value)} chars)"
            )

    logger.info(
        f"[ResearchSynthesizer] Persisted {persisted_count}/{len(DOMAIN_OUTPUT_KEYS)} "
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

    # Layer the domain output persistence callback on top of existing callbacks.
    # The agent's after_model_callback is currently a list from create_plan_react_agent.
    existing_after_model = agent.after_model_callback
    if isinstance(existing_after_model, list):
        agent.after_model_callback = (
            existing_after_model + [_persist_domain_outputs_after_model]
        )
    elif existing_after_model is not None:
        agent.after_model_callback = [
            existing_after_model,
            _persist_domain_outputs_after_model,
        ]
    else:
        agent.after_model_callback = _persist_domain_outputs_after_model

    logger.info(
        f"Created ResearchSynthesizer for {company_name} "
        f"(domains={len(DOMAIN_OUTPUT_KEYS)})"
    )
    return agent
