"""Research synthesizer agent: converts search evidence into per-domain output keys.

This agent bridges the gap between the unified QueryGeneratorAgent (which
produces search queries and executes searches) and the downstream synthesis
agents (AlignmentAnalyst, ReportCompiler) that expect structured per-domain
output keys like ``firmographicsagent_output``, ``geographicagent_output``, etc.

Persistence is deliberately layered (see tools/domain_outputs.py). The primary
path is the ``save_domain_output`` tool, which writes one domain per call so a
truncated or malformed final message can no longer cost the entire research
phase. Parsing the final message and sweeping the session events remain as
backstops.
"""

from __future__ import annotations

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse

from ......core.config import settings
from ......core.logging_config import logger
from ..prompts.synthesis_research_prompts import (
    DOMAIN_OUTPUT_KEYS,
    RESEARCH_SYNTHESIZER_PROMPT,
)
from ..tools.domain_outputs import (
    SAVE_DOMAIN_OUTPUT_TOOL,
    log_domain_progress,
    missing_domain_keys,
    recover_domain_outputs,
    save_domain_output_tool,
)
from ..tools.output_persistence import collect_agent_visible_text
from .leaf import create_plan_react_agent

SYNTHESIZER_NAME = "ResearchSynthesizer"


def _visible_text(llm_response: LlmResponse) -> str:
    content = llm_response.content
    if not content or not content.parts:
        return ""
    return "\n".join(
        (p.text or "").strip()
        for p in content.parts
        if getattr(p, "text", None) and not getattr(p, "thought", False)
    ).strip()


def _inject_domain_progress_before_model(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    """Tell the model which domains are already stored.

    Domain state survives a retry of this agent, so a second attempt should
    spend its budget on the gaps instead of re-researching what is already
    saved.
    """
    missing = missing_domain_keys(callback_context.state)
    if not missing or len(missing) == len(DOMAIN_OUTPUT_KEYS):
        return None
    try:
        llm_request.append_instructions(
            [
                f"{len(DOMAIN_OUTPUT_KEYS) - len(missing)} of "
                f"{len(DOMAIN_OUTPUT_KEYS)} domains are already saved. Research "
                f"and save ONLY these remaining domains via "
                f"{SAVE_DOMAIN_OUTPUT_TOOL}: {', '.join(missing)}."
            ]
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"[DomainOutput] Could not inject progress hint: {exc}")
    return None


def _persist_domain_outputs_after_model(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    """Backstop: recover domain payloads from a JSON-bearing model message.

    The tool path has usually stored the domains already; anything found here
    only fills keys that are still empty.
    """
    if not missing_domain_keys(callback_context.state):
        return None

    text = _visible_text(llm_response)
    if not text or "{" not in text:
        return None

    recover_domain_outputs(callback_context.state, text, source="after_model")
    return None


def _recover_domain_outputs_after_agent(
    callback_context: CallbackContext,
) -> None:
    """Last-chance sweep over every message this agent produced.

    Runs before the domain-output gate in the generic after_agent callback, so
    anything recoverable is recovered before the job is allowed to abort.
    """
    state = callback_context.state
    if not missing_domain_keys(state):
        log_domain_progress(state, stage="after_agent")
        return None

    texts: list[str] = []
    try:
        texts.append(
            collect_agent_visible_text(
                callback_context.session.events,
                agent_name=SYNTHESIZER_NAME,
                invocation_id=callback_context.invocation_id,
            )
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[DomainOutput] Could not read session events: {exc}")

    output = state.get("research_synthesizer_output")
    if output:
        texts.append(str(output))

    for index, text in enumerate(texts):
        if not text or "{" not in text:
            continue
        recover_domain_outputs(state, text, source=f"after_agent[{index}]")
        if not missing_domain_keys(state):
            break

    log_domain_progress(state, stage="after_agent")
    return None


def _prepend_callback(agent, attribute: str, callback) -> None:
    """Put *callback* first in an ADK callback slot.

    ADK stops walking a callback list at the first callback returning a truthy
    value, so anything appended after an observational callback that returns
    the response is silently dead code.
    """
    existing = getattr(agent, attribute, None)
    if isinstance(existing, list):
        setattr(agent, attribute, [callback, *existing])
    elif existing is not None:
        setattr(agent, attribute, [callback, existing])
    else:
        setattr(agent, attribute, callback)


def create_research_synthesizer(company_name: str = "Unknown"):
    """Create the research synthesizer agent.

    This agent takes the query plan from QueryGeneratorAgent, executes web
    searches, and saves the 12 per-domain output keys that AlignmentAnalyst
    and ReportCompiler expect.
    """
    agent = create_plan_react_agent(
        name=SYNTHESIZER_NAME,
        instruction=RESEARCH_SYNTHESIZER_PROMPT,
        output_key="research_synthesizer_output",
        description=(
            "Conducts web research based on the query plan and synthesizes "
            "findings into structured per-domain outputs for downstream agents."
        ),
        include_web_search=True,
        include_bm25_verify=True,
        extra_tools=[save_domain_output_tool],
        model=settings.GEMINI_MODEL,
    )

    _prepend_callback(
        agent, "before_model_callback", _inject_domain_progress_before_model
    )
    _prepend_callback(
        agent, "after_model_callback", _persist_domain_outputs_after_model
    )
    _prepend_callback(
        agent, "after_agent_callback", _recover_domain_outputs_after_agent
    )

    logger.info(
        f"Created ResearchSynthesizer for {company_name} "
        f"(domains={len(DOMAIN_OUTPUT_KEYS)})"
    )
    return agent
