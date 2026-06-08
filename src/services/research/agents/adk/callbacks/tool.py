"""Tool-level ADK callbacks."""

from __future__ import annotations

from typing import Any

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

from ......core.logging_config import logger
from ......utils.url_utils import is_authoritative
from ...sales.tools.evidence import append_evidence, evidence_key
from ...sales.tools.verification import EvidenceStore
from .common import (
    _QUERY_INJECTION_PATTERNS,
    _SNIPPET_INJECTION_SIGNALS,
    record_callback_span_event,
)

__all__ = ["before_tool_callback", "after_tool_callback"]


def before_tool_callback(
    tool: BaseTool, args: dict[str, Any], tool_context: ToolContext
) -> dict[str, Any] | None:
    tool_name = tool.name
    logger.info(
        f"\n[Callback] BEFORE TOOL Calling '{tool_name}' with original args: {args}"
    )
    record_callback_span_event(
        "adk.before_tool",
        {"tool_name": tool_name, "has_args": bool(args)},
    )

    if tool_name == "google_search":
        query = args.get("query", "")
        if any(p in query.lower() for p in _QUERY_INJECTION_PATTERNS):
            logger.warning(f"[Validation] Blocked injected search query: {query!r}")
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
    record_callback_span_event("adk.after_tool", {"tool_name": tool_name})

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

                append_evidence(state, agent_name, entries)
                EvidenceStore(state, agent_name=agent_name).ingest_grounding(
                    None, agent_name=agent_name
                )
                logger.debug(
                    f"[Callback] {evidence_key(agent_name)}: +{len(entries)} entries "
                    f"agent={agent_name} query={query!r}"
                )
    except Exception as e:  # pragma: no cover
        logger.debug(f"[Callback] Could not cache search results: {e}")

    return None


def _extract_search_entries(
    tool_response: Any,
    query: str,
    agent_name: str,
) -> list[dict]:
    """Extract individual search result entries from a google_search tool response."""
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
        for result in results:
            if not isinstance(result, dict):
                continue
            url = result.get("url") or result.get("link") or result.get("href") or ""
            title = result.get("title") or result.get("name") or ""
            snippet = (
                result.get("snippet")
                or result.get("description")
                or result.get("body")
                or ""
            )
            entries.append(_make_entry(url, title, snippet))

    def _parse_dict_response(resp: dict) -> bool:
        for key in ("results", "organic_results", "items", "webPages"):
            results = resp.get(key)
            if isinstance(results, list) and results:
                _parse_results_list(results)
                return True
        if "url" in resp or "link" in resp:
            _parse_results_list([resp])
            return True
        return False

    if hasattr(tool_response, "parts") and tool_response.parts:
        for part in tool_response.parts:
            function_response = getattr(part, "function_response", None)
            if function_response is not None:
                response = getattr(function_response, "response", None)
                if isinstance(response, dict):
                    _parse_dict_response(response)
                continue
            text = getattr(part, "text", None)
            if text:
                entries.append(_make_entry("", f"search: {query}", text[:600]))
    elif isinstance(tool_response, dict):
        if not _parse_dict_response(tool_response):
            entries.append(
                _make_entry("", f"search: {query}", str(tool_response)[:600])
            )
    elif isinstance(tool_response, str) and tool_response:
        entries.append(_make_entry("", f"search: {query}", tool_response[:600]))

    return entries
