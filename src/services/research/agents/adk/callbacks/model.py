"""Model-level ADK callbacks (before/after model)."""

from __future__ import annotations

import json
import re

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

from ......core.config import settings
from ......core.logging_config import logger
from ......utils.guardrails import InputGuardrail
from ......utils.url_utils import is_authoritative
from ....run.resilience.state import pop_retry_hint
from ....utils.model_pricing import (
    extract_usage_counts,
    pop_invocation_model,
    record_token_usage,
    resolve_agent_model,
    store_invocation_model,
)
from ...sales.tools.evidence import append_evidence, evidence_key
from .common import record_callback_span_event

__all__ = ["before_model_callback", "after_model_callback"]


def _render_report_prompt_template(prompt: str, session_state: dict) -> str:
    """Substitute {{variable?}} placeholders with session state values for ReportCompiler."""
    template_vars = [
        "company_name",
        "firmographicsagent_output",
        "geographicagent_output",
        "executiveagent_output",
        "strategyagent_output",
        "complianceagent_output",
        "marketagent_output",
        "ecosystemagent_output",
        "techstackagent_output",
        "procurementagent_output",
        "growthsignals_output",
        "risksignals_output",
        "campaignsignals_output",
        "alignment_output",
    ]

    rendered = prompt
    for var in template_vars:
        pattern = r"\{\{" + var + r"\?\}\}"
        value = session_state.get(var, "")

        if isinstance(value, (dict, list)):
            try:
                value = json.dumps(value, indent=2)
            except (TypeError, ValueError):
                value = str(value)
        else:
            value = str(value) if value else ""

        rendered = re.sub(pattern, value, rendered)

    return rendered


def _inject_session_state_into_report_prompt(
    llm_request: LlmRequest, session_state: dict
) -> None:
    """Inject session state values into ReportCompiler prompt by rendering template variables."""
    if not llm_request.contents:
        return

    for content in llm_request.contents:
        if getattr(content, "role", None) == "user" and content.parts:
            for part in content.parts:
                if getattr(part, "text", None):
                    original_text = part.text
                    rendered_text = _render_report_prompt_template(
                        original_text, session_state
                    )
                    if rendered_text != original_text:
                        part.text = rendered_text
                        logger.info(
                            "[Callback] Rendered ReportCompiler prompt template "
                            f"({len(original_text)} -> {len(rendered_text)} chars)"
                        )


def before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    logger.info(
        f"[Callback] Before model callback for {agent_name} : invocation id :{invocation_id}"
    )
    record_callback_span_event(
        "adk.before_model",
        {"agent_name": agent_name, "invocation_id": invocation_id},
    )

    try:
        if agent_name == "ReportCompiler" and llm_request.contents:
            _inject_session_state_into_report_prompt(
                llm_request, callback_context.state
            )
    except Exception as e:  # pragma: no cover
        logger.debug(
            f"[Callback] Failed to inject session state into ReportCompiler prompt: {e}"
        )

    try:
        if "mc_temperature" not in callback_context.state:
            config = getattr(llm_request, "config", None)
            if config is not None:
                temp = getattr(config, "temperature", None)
                if temp is not None:
                    callback_context.state["mc_temperature"] = temp
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Could not capture temperature: {e}")

    try:
        if agent_name != "ReportCompiler" and llm_request.contents:
            for content in reversed(llm_request.contents):
                if getattr(content, "role", None) == "user" and content.parts:
                    user_text = " ".join(
                        p.text for p in content.parts if getattr(p, "text", None)
                    )
                    if user_text:
                        guardrail = InputGuardrail()
                        violations = guardrail.scan_jailbreak(user_text)
                        if violations:
                            rules = ", ".join(v.rule for v in violations)
                            logger.warning(
                                f"[Callback] Jailbreak attempt detected in LLM request "
                                f"agent={agent_name} violations={rules}"
                            )
                            return _create_validation_error_response(
                                f"Request blocked by input guardrails: {rules}"
                            )
                        break
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Jailbreak scan in callback failed: {e}")

    _inject_retry_hint(callback_context, llm_request)

    try:
        model = getattr(llm_request, "model", None)
        if model:
            store_invocation_model(callback_context.state, invocation_id, str(model))
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Could not capture model name: {e}")

    return None


def after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse | None:
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    logger.info(
        f"[Callback] After model callback for {agent_name} : invocation id :{invocation_id}"
    )

    try:
        usage = getattr(llm_response, "usage_metadata", None)
        if usage is not None:
            input_t, output_t = extract_usage_counts(usage)
            model = pop_invocation_model(callback_context.state, invocation_id)
            if not model:
                model = resolve_agent_model(agent_name)
            record_token_usage(callback_context.state, model, input_t, output_t)
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Could not accumulate token counts: {e}")

    record_callback_span_event(
        "adk.after_model",
        {
            "agent_name": agent_name,
            "invocation_id": invocation_id,
            "input_tokens": int(callback_context.state.get("mc_input_tokens") or 0),
            "output_tokens": int(callback_context.state.get("mc_output_tokens") or 0),
        },
    )

    try:
        has_content = bool(
            llm_response.content and getattr(llm_response.content, "parts", None)
        )
        capture_mode = settings.OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
        record_callback_span_event(
            "adk.llm_content_capture",
            {
                "agent_name": agent_name,
                "capture_mode": capture_mode,
                "has_content": has_content,
                "parts_count": len(llm_response.content.parts) if has_content else 0,
            },
        )
    except Exception:  # pragma: no cover
        pass

    try:
        candidates = getattr(llm_response, "candidates", None)
        if not candidates and hasattr(llm_response, "response"):
            candidates = getattr(llm_response.response, "candidates", None)

        candidates = candidates or []
        if candidates:
            metadata = getattr(candidates[0], "grounding_metadata", None) or getattr(
                candidates[0], "groundingMetadata", None
            )

            if metadata:
                logger.debug(
                    f"[Callback] Found grounding metadata in response from {agent_name}"
                )
                grounding_entries = []

                chunks = (
                    getattr(metadata, "grounding_chunks", None)
                    or getattr(metadata, "groundingChunks", [])
                    or []
                )
                supports = (
                    getattr(metadata, "grounding_supports", None)
                    or getattr(metadata, "groundingSupports", [])
                    or []
                )

                logger.debug(
                    f"[Callback] {len(chunks)} chunks, {len(supports)} supports found"
                )
                for support in supports:
                    segment = getattr(support, "segment", None)
                    text = getattr(segment, "text", "") if segment else ""

                    indices = (
                        getattr(support, "grounding_chunk_indices", None)
                        or getattr(support, "groundingChunkIndices", [])
                        or []
                    )

                    for idx in indices:
                        if idx < len(chunks):
                            chunk = chunks[idx]
                            web = getattr(chunk, "web", None) or getattr(
                                chunk, "get", lambda x, default=None: None
                            )("web")
                            if web:
                                uri = getattr(web, "uri", None) or getattr(
                                    web, "get", lambda x, default=None: None
                                )("uri")
                                title = getattr(web, "title", None) or getattr(
                                    web,
                                    "get",
                                    lambda x, default="Grounded Source": default,
                                )("title")
                                if uri:
                                    grounding_entries.append(
                                        {
                                            "url": uri,
                                            "title": title,
                                            "snippet": text,
                                            "query": "Native Grounding Search",
                                            "agent": agent_name,
                                            "authoritative": is_authoritative(uri),
                                        }
                                    )

                if grounding_entries:
                    append_evidence(
                        callback_context.state, agent_name, grounding_entries
                    )
                    logger.debug(
                        f"[Callback] Captured {len(grounding_entries)} grounding snippets "
                        f"from {agent_name} model response -> {evidence_key(agent_name)}"
                    )

                for support in supports:
                    scores = getattr(support, "confidence_scores", None) or []
                    if any(s < 0.7 for s in scores):
                        text = getattr(getattr(support, "segment", None), "text", "")
                        logger.warning(
                            f"[Callback] Low-confidence grounding: "
                            f"min={min(scores):.2f} claim={text[:100]!r} agent={agent_name}"
                        )
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Grounding metadata inspection failed: {e}")

    # Must return None, not llm_response. ADK stops walking
    # canonical_after_model_callbacks at the first truthy return, so returning
    # the (unmodified) response here silently disables every callback
    # registered after this one. This callback is observational only.
    return None


def _create_validation_error_response(error_message: str) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    text=f"[Input Validation Error] {error_message}. "
                    "Please review the input and try again."
                )
            ],
        )
    )


def _inject_retry_hint(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> None:
    """Append one-shot retry guidance for the current agent when available."""
    hint = pop_retry_hint(callback_context.state, callback_context.agent_name)
    if not hint:
        return
    try:
        llm_request.append_instructions([hint])
        logger.info(
            f"[Retry] Injected retry hint for agent={callback_context.agent_name} "
            f"({len(hint)} chars)"
        )
    except Exception as exc:  # pragma: no cover
        logger.warning(
            f"[Retry] Could not append retry hint for "
            f"agent={callback_context.agent_name}: {exc}"
        )
