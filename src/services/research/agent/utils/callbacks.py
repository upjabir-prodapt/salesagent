"""Public callback facade preserving the legacy callback API."""

from __future__ import annotations

from .callback_agent import after_agent_callback, before_agent_callback
from .callback_model import after_model_callback, before_model_callback
from .callback_tool import after_tool_callback, before_tool_callback

__all__ = [
    "before_model_callback",
    "after_model_callback",
    "before_agent_callback",
    "after_agent_callback",
    "before_tool_callback",
    "after_tool_callback",
]

"""
ADK Callbacks Module

Implements comprehensive callback handlers for agent lifecycle and LLM interaction monitoring.
Provides four callback types:
- before_model_callback: Called before LLM requests
- after_model_callback: Called after LLM responses
- before_agent_callback: Called before agent execution
- after_agent_callback: Called after agent completion
"""

from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.tools.base_tool import BaseTool  # Required for type hinting in callback
from google.adk.tools.tool_context import ToolContext
from google.genai import types
from opentelemetry import trace

from src.core.config import settings
from src.core.exceptions import InputValidationException
from src.core.logging_config import logger

from .....utils.guardrails import InputGuardrail
from .....utils.url_utils import is_authoritative
from ..sales.utils.evidence import append_evidence, evidence_key
from ..sales.utils.output_persistence import persist_output_from_session_events
from ..sales.utils.verification import Bm25Verifier, EvidenceStore
from .agent_pipeline import (
    get_output_key,
    is_tracked_agent,
    validate_agent_output,
)
from .retry_state import pop_retry_hint
from .telemetry import track_agent_end, track_agent_start

_QUERY_INJECTION_PATTERNS = [
    "ignore previous",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "new instructions:",
    "system prompt",
    "jailbreak",
]

_SNIPPET_INJECTION_SIGNALS = [
    "ignore previous",
    "ignore all instructions",
    "you are now",
    "disregard your",
    "new instructions:",
    "override your",
]


def _record_callback_span_event(
    event_name: str, attributes: dict[str, Any] | None = None
) -> None:
    """Attach lightweight callback events to the current span when available."""
    current_span = trace.get_current_span()
    if not current_span:
        return
    span_context = current_span.get_span_context()
    if not span_context or not span_context.is_valid:
        return
    current_span.add_event(event_name, attributes=attributes or {})


# ============================================================================
# BEFORE MODEL CALLBACK
# ============================================================================


def before_model_callback(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> LlmResponse | None:
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    logger.info(
        f"[Callback] Before model callback for {agent_name} : invocation id :{invocation_id}"
    )
    _record_callback_span_event(
        "adk.before_model",
        {"agent_name": agent_name, "invocation_id": invocation_id},
    )

    # Capture temperature into session state (first non-None value wins)
    try:
        if "mc_temperature" not in callback_context.state:
            config = getattr(llm_request, "config", None)
            if config is not None:
                temp = getattr(config, "temperature", None)
                if temp is not None:
                    callback_context.state["mc_temperature"] = temp
    except Exception as e:
        logger.debug(f"[Callback] Could not capture temperature: {e}")

    # Secondary jailbreak scan on the most recent user-role message
    # (guards against prompt injection arriving through tool results or context)
    # Skip for ReportCompiler as it processes trusted internal agent outputs
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
                        break  # only scan the most recent user message
    except Exception as e:
        logger.debug(f"[Callback] Jailbreak scan in callback failed: {e}")

    _inject_retry_hint(callback_context, llm_request)
    return None


# ============================================================================
# AFTER MODEL CALLBACK
# ============================================================================


def after_model_callback(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> LlmResponse:
    """
    Called just after a response is received from the LLM.

    Performs:
    - Response logging
    - Safety validation
    - Metadata extraction
    - Performance tracking

    Args:
        callback_context: Context containing agent name, invocation ID, and state
        llm_response: The response received from the LLM

    Returns:
        Modified LlmResponse (usually unchanged unless filtering applied)
    """
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id
    logger.info(
        f"[Callback] After model callback for {agent_name} : invocation id :{invocation_id}"
    )

    # Accumulate token counts from usage_metadata
    try:
        usage = getattr(llm_response, "usage_metadata", None)
        if usage is not None:
            # google-genai SDK uses prompt_token_count / candidates_token_count
            input_t = (
                getattr(usage, "prompt_token_count", None)
                or getattr(usage, "input_token_count", None)
                or 0
            )
            output_t = (
                getattr(usage, "candidates_token_count", None)
                or getattr(usage, "output_token_count", None)
                or 0
            )
            prev_in = callback_context.state.get("mc_input_tokens") or 0
            prev_out = callback_context.state.get("mc_output_tokens") or 0
            callback_context.state["mc_input_tokens"] = prev_in + input_t
            callback_context.state["mc_output_tokens"] = prev_out + output_t
    except Exception as e:
        logger.debug(f"[Callback] Could not accumulate token counts: {e}")
    _record_callback_span_event(
        "adk.after_model",
        {
            "agent_name": agent_name,
            "invocation_id": invocation_id,
            "input_tokens": int(callback_context.state.get("mc_input_tokens") or 0),
            "output_tokens": int(callback_context.state.get("mc_output_tokens") or 0),
        },
    )

    # Annotate content capture status for OTEL observability
    try:
        has_content = bool(
            llm_response.content
            and getattr(llm_response.content, "parts", None)
        )
        capture_mode = settings.OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
        _record_callback_span_event(
            "adk.llm_content_capture",
            {
                "agent_name": agent_name,
                "capture_mode": capture_mode,
                "has_content": has_content,
                "parts_count": len(llm_response.content.parts) if has_content else 0,
            },
        )
    except Exception:
        pass

    # Capturing grounding metadata as search evidence
    try:
        # Resolve candidates from ADK wrapper or raw response
        candidates = getattr(llm_response, "candidates", None)
        if not candidates and hasattr(llm_response, "response"):
            candidates = getattr(llm_response.response, "candidates", None)

        candidates = candidates or []
        if candidates:
            # Check both snake_case (SDK) and camelCase (JSON/ADK)
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
                            # Web attribute might be a dict or object
                            web = getattr(chunk, "web", None) or getattr(
                                chunk, "get", lambda x, default=None: None
                            )("web")
                            if web:
                                # Access as attribute or dict key
                                uri = getattr(web, "uri", None) or getattr(
                                    web, "get", lambda x, default=None: None
                                )("uri")
                                title = getattr(web, "title", None) or getattr(
                                    web, "get", lambda x, default="Grounded Source": default
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

                # Confidence logging
                for support in supports:
                    scores = getattr(support, "confidence_scores", None) or []
                    if any(s < 0.7 for s in scores):
                        text = getattr(getattr(support, "segment", None), "text", "")
                        logger.warning(
                            f"[Callback] Low-confidence grounding: "
                            f"min={min(scores):.2f} claim={text[:100]!r} agent={agent_name}"
                        )
    except Exception as e:
        logger.debug(f"[Callback] Grounding metadata inspection failed: {e}")

    return llm_response


# ============================================================================
# BEFORE AGENT CALLBACK
# ============================================================================


async def before_agent_callback(callback_context: CallbackContext) -> None:
    """
    Called immediately before the agent's _run_async_impl method executes.
    """
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id

    # STAGGER LOGIC: Prevent QPM/Quota bursts in ParallelAgent environments
    # AstraZeneca/Large companies trigger many parallel searches. Spacing them out
    # by 1-5 seconds prevents the "Resource Exhausted" chain reaction.
    try:
        import asyncio
        import random

        # Don't stagger the main orchestrator, only the research/signals agents
        _PARALLEL_RESEARCHERS = {
            "FirmographicsGeographicAgent",
            "ExecutiveAgent",
            "StrategyComplianceAgent",
            "MarketEcosystemAgent",
            "TechStackAgent",
            "SignalsOrchestrator",
            "GrowthSignals",
            "RiskSignals",
            "CampaignSignals",
        }

        if agent_name in _PARALLEL_RESEARCHERS:
            # Use asyncio.sleep to avoid blocking the event loop while staggering
            delay = random.uniform(1.0, 5.0)
            logger.debug(
                f"[Callback] Staggering {agent_name} start by {delay:.2f}s to protect quota"
            )
            await asyncio.sleep(delay)
    except Exception as e:
        logger.debug(f"[Callback] Staggering failed for {agent_name}: {e}")

    # Log agent entry
    logger.info(
        f"[Callback] Before Agent starting: {agent_name}",
        extra={"agent": agent_name, "invocation": invocation_id},
    )
    _record_callback_span_event(
        "adk.before_agent",
        {"agent_name": agent_name, "invocation_id": invocation_id},
    )

    # Record per-agent start snapshot for telemetry
    try:
        track_agent_start(callback_context)
    except Exception as e:
        logger.debug(f"[Telemetry] track_agent_start failed for {agent_name}: {e}")

    # Track current executing agent in session state (for status endpoint)
    try:
        callback_context.state["current_executing_agent"] = agent_name
        agent_status_map = dict(callback_context.state.get("agent_status_map") or {})
        agent_status_map[agent_name] = "running"
        callback_context.state["agent_status_map"] = agent_status_map
    except Exception as e:
        logger.debug(f"[Callback] Could not set current_executing_agent: {e}")

    # Block downstream synthesis if a required upstream output is missing.
    if callback_context.agent_name == "ReportCompiler":
        validate_agent_output(callback_context.state, "AlignmentAnalyst")

    return None


# ============================================================================
# AFTER AGENT CALLBACK
# ============================================================================


def after_agent_callback(callback_context: CallbackContext) -> types.Content | None:
    """
    Called after agent completes execution.

    Performs:
    - Agent exit logging
    - Duration tracking
    - Metrics collection
    - Resource cleanup

    Args:
        callback_context: Context containing agent name, invocation ID, and state

    Returns:
        None to use agent's original output, or Content to replace output
    """

    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id

    logger.info(
        f"[Callback] After Agent starting: {agent_name}",
        extra={"agent": agent_name, "invocation": invocation_id},
    )
    _record_callback_span_event(
        "adk.after_agent",
        {"agent_name": agent_name, "invocation_id": invocation_id},
    )

    # Compute per-agent telemetry and accumulate record in session state
    try:
        track_agent_end(callback_context)
    except Exception as e:
        logger.debug(f"[Telemetry] track_agent_end failed for {agent_name}: {e}")

    output_key = get_output_key(agent_name)
    if is_tracked_agent(agent_name) and output_key:
        persist_output_from_session_events(
            callback_context.state,
            callback_context.session.events,
            agent_name=agent_name,
            output_key=output_key,
            invocation_id=callback_context.invocation_id,
        )

    # Update agent status map — only "completed" when required output_key is present.
    try:
        agent_status_map = dict(callback_context.state.get("agent_status_map") or {})
        if is_tracked_agent(agent_name) and output_key:
            value = callback_context.state.get(output_key)
            if value is None or not str(value).strip():
                agent_status_map[agent_name] = "failed_missing_output"
                logger.warning(
                    "[Callback] Agent %s ended without output_key %r",
                    agent_name,
                    output_key,
                )
            else:
                callback_context.state["last_completed_agent"] = agent_name
                agent_status_map[agent_name] = "completed"
        else:
            callback_context.state["last_completed_agent"] = agent_name
            agent_status_map[agent_name] = "completed"
        callback_context.state["agent_status_map"] = agent_status_map
    except Exception as e:
        logger.debug(f"[Callback] Could not update agent_status_map: {e}")

    if agent_name == "ReportCompiler":
        validation_status = callback_context.state.get("report_validation_status")
        if validation_status and validation_status != "PASSED":
            logger.warning(
                f"[ReportCompiler] Completed without PASSED validation "
                f"(status={validation_status!r}); clearing final_report"
            )
            callback_context.state["final_report"] = ""

    if is_tracked_agent(agent_name):
        validate_agent_output(callback_context.state, agent_name)
        _record_bm25_telemetry(callback_context, agent_name)

    # Use agent's original output
    return None


def _record_bm25_telemetry(callback_context: CallbackContext, agent_name: str) -> None:
    """Optional post-agent BM25 telemetry (no retry)."""
    if agent_name in ("ReportCompiler", "AlignmentAnalyst"):
        return
    output_key = get_output_key(agent_name)
    if not output_key:
        return
    draft = callback_context.state.get(output_key)
    if not draft or not str(draft).strip():
        return
    session_id = "unknown"
    try:
        session_id = str(getattr(callback_context, "session", None) and callback_context.session.id)
    except Exception:
        pass
    try:
        result = Bm25Verifier().verify(
            str(draft),
            callback_context.state,
            agent_name=agent_name,
            session_id=session_id,
        )
        callback_context.state[f"{agent_name}_bm25_status"] = result.status
        callback_context.state[f"{agent_name}_bm25_unsupported"] = result.unsupported[:8]
    except Exception as e:
        logger.debug(f"[Callback] BM25 telemetry failed for {agent_name}: {e}")


def before_tool_callback(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    tool_name = tool.name
    logger.info(
        f"\n[Callback] BEFORE TOOL Calling '{tool_name}' with original args: {args}"
    )
    _record_callback_span_event(
        "adk.before_tool",
        {"tool_name": tool_name, "has_args": bool(args)},
    )

    if tool_name == "google_search":
        query = args.get("query", "")
        if any(p in query.lower() for p in _QUERY_INJECTION_PATTERNS):
            logger.warning(f"[Callback] Blocked injected search query: {query!r}")
            return {"error": "Search query blocked by input policy"}

    return None


def after_tool_callback(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: types.Content | types.GenerateContentResponse,
) -> dict[str, Any] | None:
    tool_name = tool.name
    logger.info(f"[Callback] AFTER TOOL '{tool_name}' returned: {tool_response}")
    _record_callback_span_event(
        "adk.after_tool",
        {"tool_name": tool_name},
    )

    # Count tool calls as sources crawled if they are web tools
    _WEB_TOOLS = {"google_search", "google_search_agent", "read_url"}
    try:
        state = tool_context.callback_context.state
        if tool_name in _WEB_TOOLS:
            state["mc_tool_call_count"] = state.get("mc_tool_call_count", 0) + 1

            # For read_url, extract domain directly from args
            if tool_name == "read_url" and "url" in args:
                from urllib.parse import urlparse

                domain = urlparse(args["url"]).netloc
                if domain:
                    domains = list(state.get("mc_source_domains") or [])
                    if domain not in domains:
                        domains.append(domain)
                        state["mc_source_domains"] = domains
    except Exception as e:
        logger.debug(f"[Callback] Could not increment tool call count: {e}")

    # Ingest web search tool results into per-agent evidence registry.
    try:
        if tool_name in ("google_search", "google_search_agent"):
            agent_name = getattr(tool_context.callback_context, "agent_name", "unknown")
            query = args.get("query", "") or args.get("request", "")
            entries = _extract_search_entries(tool_response, query, agent_name)
            if entries:
                state = tool_context.callback_context.state
                for entry in entries:
                    url = entry.get("url", "")
                    snippet = entry.get("snippet", "").lower()

                    if any(sig in snippet for sig in _SNIPPET_INJECTION_SIGNALS):
                        logger.warning(
                            f"[Callback] Prompt injection in snippet: "
                            f"url={url} agent={agent_name}"
                        )
                        entry["flagged_injection"] = True

                    entry["authoritative"] = is_authoritative(url) if url else False
                    if url and not entry["authoritative"]:
                        logger.warning(
                            f"[Callback] Non-authoritative source: {url} agent={agent_name}"
                        )

                    if url:
                        from urllib.parse import urlparse

                        domain = urlparse(url).netloc
                        if domain:
                            domains = list(state.get("mc_source_domains") or [])
                            if domain not in domains:
                                domains.append(domain)
                            state["mc_source_domains"] = domains

                append_evidence(state, agent_name, entries)
                EvidenceStore(state, agent_name=agent_name).ingest_grounding(
                    None, agent_name=agent_name
                )
                logger.debug(
                    f"[Callback] {evidence_key(agent_name)}: +{len(entries)} entries "
                    f"agent={agent_name} query={query!r}"
                )
    except Exception as e:
        logger.debug(f"[Callback] Could not cache search results: {e}")

    return None


def _extract_search_entries(
    tool_response: Any,
    query: str,
    agent_name: str,
) -> list[dict]:
    """
    Extract individual search result entries from a google_search tool response.

    Handles multiple response formats defensively:
    - types.Content with function_response parts (structured dict)
    - types.Content with text parts (plain text blob)
    - Raw dict with 'results' / 'organic_results' key
    - Plain string fallback
    """
    entries: list[dict] = []

    def _make_entry(url: str, title: str, snippet: str) -> dict:
        return {
            "url": url.strip(),
            "title": title.strip()[:200],
            "snippet": snippet.strip()[:600],
            "query": query,
            "agent": agent_name,
        }

    def _parse_results_list(results: list) -> None:
        for r in results:
            if not isinstance(r, dict):
                continue
            url = r.get("url") or r.get("link") or r.get("href") or ""
            title = r.get("title") or r.get("name") or ""
            snippet = r.get("snippet") or r.get("description") or r.get("body") or ""
            entries.append(_make_entry(url, title, snippet))

    def _parse_dict_response(resp: dict) -> bool:
        """Try common result container keys; return True if any were found."""
        for key in ("results", "organic_results", "items", "webPages"):
            results = resp.get(key)
            if isinstance(results, list) and results:
                _parse_results_list(results)
                return True
        # Flat dict that IS a single result
        if "url" in resp or "link" in resp:
            _parse_results_list([resp])
            return True
        return False

    # --- Format 1: types.Content with parts ---
    if hasattr(tool_response, "parts") and tool_response.parts:
        for part in tool_response.parts:
            # Structured function_response part
            fr = getattr(part, "function_response", None)
            if fr is not None:
                resp = getattr(fr, "response", None)
                if isinstance(resp, dict):
                    _parse_dict_response(resp)
                continue
            # Plain text part
            text = getattr(part, "text", None)
            if text:
                entries.append(_make_entry("", f"search: {query}", text[:600]))

    # --- Format 2: raw dict ---
    elif isinstance(tool_response, dict):
        if not _parse_dict_response(tool_response):
            entries.append(
                _make_entry("", f"search: {query}", str(tool_response)[:600])
            )

    # --- Format 3: plain string ---
    elif isinstance(tool_response, str) and tool_response:
        entries.append(_make_entry("", f"search: {query}", tool_response[:600]))

    return entries


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _log_model_request(
    agent_name: str, llm_request: LlmRequest, request_id: str, invocation_id: str
) -> None:
    """Log LLM request details (sanitized)."""
    try:
        # Extract last user message
        last_message = ""
        if llm_request.contents and len(llm_request.contents) > 0:
            last_content = llm_request.contents[-1]
            if last_content.role == "user" and last_content.parts:
                last_message = last_content.parts[0].text[:200]  # First 200 chars

        logger.info(
            "[Callback] LLM request initiated",
            extra={
                "agent": agent_name,
                "request_id": request_id,
                "invocation": invocation_id,
                "message_preview": last_message,
                "num_contents": len(llm_request.contents) if llm_request.contents else 0,
            },
        )
    except Exception as e:
        logger.warning(f"[Callback] Failed to log request: {e}")


def _log_model_response(
    agent_name: str, llm_response: LlmResponse, request_id: str, invocation_id: str
) -> None:
    """Log LLM response details (sanitized)."""
    try:
        # Extract response text preview
        response_preview = ""
        if llm_response.content and llm_response.content.parts:
            response_preview = llm_response.content.parts[0].text[
                :200
            ]  # First 200 chars

        logger.info(
            "[Callback] LLM response received",
            extra={
                "agent": agent_name,
                "request_id": request_id,
                "invocation": invocation_id,
                "response_preview": response_preview,
                "has_content": bool(llm_response.content),
            },
        )
    except Exception as e:
        logger.warning(f"[Callback] Failed to log response: {e}")


def _validate_llm_request(llm_request: LlmRequest) -> None:
    """
    Validate LLM request meets basic requirements.

    Raises:
        InputValidationException: If validation fails
    """
    if not llm_request.contents:
        raise InputValidationException("LLM request has no content")

    # Check for empty messages
    for content in llm_request.contents:
        if not content.parts or len(content.parts) == 0:
            raise InputValidationException("LLM request contains empty content")

    # Additional validation can be added here
    # e.g., check for excessive length, forbidden patterns, etc.


def _validate_response_safety(
    llm_response: LlmResponse, agent_name: str, request_id: str
) -> None:
    """Validate LLM response for safety issues."""
    try:
        # Check if response was blocked
        if hasattr(llm_response, "candidates") and llm_response.candidates:
            candidate = llm_response.candidates[0]
            if hasattr(candidate, "finish_reason"):
                finish_reason = candidate.finish_reason
                if finish_reason and "SAFETY" in str(finish_reason):
                    logger.warning(
                        "[Callback] Safety block detected in response",
                        extra={
                            "agent": agent_name,
                            "request_id": request_id,
                            "finish_reason": finish_reason,
                        },
                    )
    except Exception as e:
        logger.debug(f"[Callback] Could not validate response safety: {e}")


def _extract_response_metadata(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> None:
    """Extract and store metadata from LLM response."""
    try:
        # Store response in state for potential downstream use
        agent_name = callback_context.agent_name
        state_key = f"{agent_name}_response_received"
        callback_context.state[state_key] = True

        # Additional metadata extraction can be added here
    except Exception as e:
        logger.debug(f"[Callback] Could not extract response metadata: {e}")


def _track_request_tokens(
    agent_name: str, llm_request: LlmRequest, request_id: str
) -> None:
    """Track token usage for request (approximate)."""
    try:
        # Basic token estimation (4 chars ≈ 1 token)
        total_chars = 0
        if llm_request.contents:
            for content in llm_request.contents:
                if content.parts:
                    for part in content.parts:
                        if hasattr(part, "text") and part.text:
                            total_chars += len(part.text)

        estimated_tokens = total_chars // 4
        logger.debug(
            "[Callback] Request token estimate",
            extra={
                "agent": agent_name,
                "request_id": request_id,
                "estimated_tokens": estimated_tokens,
            },
        )
    except Exception as e:
        logger.debug(f"[Callback] Could not track tokens: {e}")


def _track_response_metrics(
    agent_name: str, llm_response: LlmResponse, request_id: str
) -> None:
    """Track metrics from LLM response."""
    try:
        # Log response characteristics
        has_content = bool(llm_response.content)
        logger.debug(
            "[Callback] Response metrics",
            extra={
                "agent": agent_name,
                "request_id": request_id,
                "has_content": has_content,
            },
        )
    except Exception as e:
        logger.debug(f"[Callback] Could not track response metrics: {e}")


def _add_request_metadata(
    callback_context: CallbackContext, llm_request: LlmRequest, request_id: str
) -> None:
    """Add metadata to LLM request."""
    try:
        # Mark that request was processed by callback
        callback_context.state["request_processed_by_callback"] = True
    except Exception as e:
        logger.debug(f"[Callback] Could not add request metadata: {e}")


def _validate_session_state(state: dict[str, Any]) -> bool:
    """
    Validate session state has required fields.

    Returns:
        True if state is valid, False otherwise
    """
    # Check for request_id
    return not ("request_id" not in state or not state["request_id"])


def _collect_agent_metrics(agent_name: str, duration: float, request_id: str) -> None:
    """Collect and log agent performance metrics."""
    try:
        logger.info(
            "[Metrics] Agent performance",
            extra={
                "agent": agent_name,
                "request_id": request_id,
                "duration_seconds": round(duration, 2),
                "duration_ms": round(duration * 1000, 0),
            },
        )

        # Additional metrics collection (e.g., to BigQuery) can be added here
    except Exception as e:
        logger.debug(f"[Callback] Could not collect agent metrics: {e}")


def _create_validation_error_response(error_message: str) -> LlmResponse:
    """
    Create an error response to return when validation fails.

    Args:
        error_message: The validation error message

    Returns:
        LlmResponse containing the error
    """
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
    except Exception as exc:  # pragma: no cover
        logger.debug(
            "Could not append retry hint for %s: %s",
            callback_context.agent_name,
            exc,
        )
