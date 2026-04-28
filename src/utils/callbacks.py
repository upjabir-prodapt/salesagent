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
from loguru import logger

from src.core.exceptions import InputValidationException

from .guardrails import InputGuardrail
from .telemetry import track_agent_end, track_agent_start

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
    try:
        if llm_request.contents:
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

    return None


# ============================================================================
# BEFORE AGENT CALLBACK
# ============================================================================


def before_agent_callback(callback_context: CallbackContext) -> None:
    """
    Called immediately before the agent's _run_async_impl method executes.

    Performs:
    - Agent entry logging
    - Session state validation
    - Performance timer initialization
    - Resource setup

    Args:
        callback_context: Context containing agent name, invocation ID, and state
    """
    agent_name = callback_context.agent_name
    invocation_id = callback_context.invocation_id

    # Log agent entry
    logger.info(
        f"[Callback] Before Agent starting: {agent_name}",
        agent=agent_name,
        invocation=invocation_id,
    )

    # Record per-agent start snapshot for telemetry
    try:
        track_agent_start(callback_context)
    except Exception as e:
        logger.debug(f"[Telemetry] track_agent_start failed for {agent_name}: {e}")

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
        agent=agent_name,
        invocation=invocation_id,
    )

    # Compute per-agent telemetry and accumulate record in session state
    try:
        track_agent_end(callback_context)
    except Exception as e:
        logger.debug(f"[Telemetry] track_agent_end failed for {agent_name}: {e}")

    # Use agent's original output
    return None


def before_tool_callback(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    tool_name = tool.name
    logger.info(
        f"\n[Callback] BEFORE TOOL Calling '{tool_name}' with original args: {args}"
    )
    return None


def after_tool_callback(
    tool: BaseTool,
    args: dict[str, Any],
    tool_context: ToolContext,
    tool_response: types.Content | types.GenerateContentResponse,
) -> dict[str, Any] | None:
    tool_name = tool.name
    logger.info(f"[Callback] AFTER TOOL '{tool_name}' returned: {tool_response}")

    # Count tool calls as sources crawled if they are web tools
    _WEB_TOOLS = {"google_search", "read_url"}
    try:
        state = tool_context.callback_context.state
        if tool_name in _WEB_TOOLS:
            state["mc_tool_call_count"] = state.get("mc_tool_call_count", 0) + 1
    except Exception as e:
        logger.debug(f"[Callback] Could not increment tool call count: {e}")

    # Collect source domains from read_url tool calls
    try:
        if tool_name == "read_url" and "url" in args:
            from urllib.parse import urlparse

            parsed = urlparse(args["url"])
            domain = parsed.netloc
            if domain:
                domains = list(tool_context.state.get("mc_source_domains") or [])
                if domain not in domains:
                    domains.append(domain)
                tool_context.state["mc_source_domains"] = domains
    except Exception as e:
        logger.debug(f"[Callback] Could not collect source domain: {e}")

    # Cache raw google_search results as ground-truth evidence for hallucination checks
    try:
        if tool_name == "google_search":
            agent_name = getattr(tool_context, "agent_name", "unknown")
            query = args.get("query", "")
            entries = _extract_search_entries(tool_response, query, agent_name)
            if entries:
                cache: list[dict] = list(
                    tool_context.state.get("raw_search_cache") or []
                )
                existing_urls = {e["url"] for e in cache if e.get("url")}
                added = 0
                for entry in entries:
                    url = entry.get("url", "")
                    if url and url in existing_urls:
                        continue
                    cache.append(entry)
                    if url:
                        existing_urls.add(url)
                    added += 1
                tool_context.state["raw_search_cache"] = cache
                logger.debug(
                    f"[Callback] raw_search_cache: +{added} entries "
                    f"(total={len(cache)}) agent={agent_name} query={query!r}"
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
            agent=agent_name,
            request_id=request_id,
            invocation=invocation_id,
            message_preview=last_message,
            num_contents=len(llm_request.contents) if llm_request.contents else 0,
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
            agent=agent_name,
            request_id=request_id,
            invocation=invocation_id,
            response_preview=response_preview,
            has_content=bool(llm_response.content),
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
                        agent=agent_name,
                        request_id=request_id,
                        finish_reason=finish_reason,
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
            agent=agent_name,
            request_id=request_id,
            estimated_tokens=estimated_tokens,
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
            agent=agent_name,
            request_id=request_id,
            has_content=has_content,
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
            agent=agent_name,
            request_id=request_id,
            duration_seconds=round(duration, 2),
            duration_ms=round(duration * 1000, 0),
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
