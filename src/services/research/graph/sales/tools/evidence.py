"""
Unified evidence registry for sales research agents.

Per-agent keys: search_evidence_{AgentName}
Job-level aggregate: aggregate_job_evidence(state) -> list[dict]
"""

from __future__ import annotations

import json
from typing import Any

from ......utils.url_utils import is_authoritative

EVIDENCE_KEY_PREFIX = "search_evidence_"
LEGACY_RAW_CACHE_PREFIX = "raw_search_cache_"
GLOBAL_LEGACY_KEY = "search_evidence"

VERIFICATION_STATUS_PREFIX = "verification_"


def evidence_key(agent_name: str) -> str:
    return f"{EVIDENCE_KEY_PREFIX}{agent_name}"


def verification_status_key(agent_name: str) -> str:
    return f"{VERIFICATION_STATUS_PREFIX}{agent_name}_status"


def verification_unsupported_key(agent_name: str) -> str:
    return f"{VERIFICATION_STATUS_PREFIX}{agent_name}_unsupported_claims"


def normalize_entry(entry: dict[str, Any], *, agent_name: str = "") -> dict[str, Any]:
    """Normalize evidence dict: uri->url, standard fields."""
    url = (str(entry.get("url") or entry.get("uri") or entry.get("link") or "")).strip()
    title = str(entry.get("title") or "").strip()[:200]
    snippet = str(
        entry.get("snippet") or entry.get("description") or entry.get("body") or ""
    ).strip()[:600]
    query = str(entry.get("query") or "").strip()
    agent = str(entry.get("agent") or agent_name or "").strip()
    normalized: dict[str, Any] = {
        "url": url,
        "title": title,
        "snippet": snippet,
        "query": query,
        "agent": agent,
    }
    if url:
        normalized["authoritative"] = bool(
            entry.get("authoritative")
            if "authoritative" in entry
            else is_authoritative(url)
        )
    if entry.get("flagged_injection"):
        normalized["flagged_injection"] = True
    return normalized


def append_evidence(
    state: Any,
    agent_name: str,
    entries: list[dict[str, Any]],
) -> int:
    """Append normalized entries to agent-scoped evidence list. Returns new length."""
    if not agent_name or not entries:
        return len(get_agent_evidence(state, agent_name))
    key = evidence_key(agent_name)
    existing = list(state.get(key) or [])
    for raw in entries:
        if isinstance(raw, dict):
            existing.append(normalize_entry(raw, agent_name=agent_name))
    state[key] = existing
    return len(existing)


def get_agent_evidence(state: dict[str, Any] | Any, agent_name: str) -> list[dict]:
    """Return evidence list for one agent."""
    if hasattr(state, "get"):
        scoped = state.get(evidence_key(agent_name))
        if isinstance(scoped, list):
            return [e for e in scoped if isinstance(e, dict)]
    return []


def _legacy_raw_cache_entries(state: dict[str, Any]) -> list[dict]:
    """Merge legacy raw_search_cache_* lists during migration."""
    merged: list[dict] = []
    for key, value in state.items():
        if key.startswith(LEGACY_RAW_CACHE_PREFIX) and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    agent = str(
                        item.get("agent") or key.split("_")[3]
                        if len(key.split("_")) > 3
                        else ""
                    )
                    merged.append(normalize_entry(item, agent_name=agent))
    legacy = state.get("raw_search_cache")
    if isinstance(legacy, list):
        for item in legacy:
            if isinstance(item, dict):
                merged.append(normalize_entry(item))
    return merged


def aggregate_job_evidence(state: dict[str, Any]) -> list[dict]:
    """Merge all search_evidence_* keys plus legacy caches."""
    seen_urls: set[str] = set()
    result: list[dict] = []

    def _add(entry: dict) -> None:
        url = entry.get("url", "").strip().lower()
        dedupe_key = url or f"{entry.get('title', '')}:{entry.get('snippet', '')[:80]}"
        if dedupe_key in seen_urls:
            return
        seen_urls.add(dedupe_key)
        result.append(entry)

    for key, value in state.items():
        if key.startswith(EVIDENCE_KEY_PREFIX) and isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    agent = key.removeprefix(EVIDENCE_KEY_PREFIX)
                    _add(normalize_entry(item, agent_name=agent))

    global_legacy = state.get(GLOBAL_LEGACY_KEY)
    if isinstance(global_legacy, list):
        for item in global_legacy:
            if isinstance(item, dict):
                _add(normalize_entry(item))

    for entry in _legacy_raw_cache_entries(state):
        _add(entry)

    return result


def evidence_to_text(entries: list[dict], *, max_chars: int | None = None) -> str:
    """Flat text blob from evidence entries (titles + snippets)."""
    parts: list[str] = []
    total = 0
    for entry in entries:
        title = str(entry.get("title", "")).strip()
        snippet = str(entry.get("snippet", "")).strip()
        block = "\n".join(p for p in (title, snippet) if p)
        if not block:
            continue
        if max_chars is not None and total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block) + 2
    return "\n\n".join(parts)


def evidence_to_block(
    entries: list[dict],
    *,
    max_chars: int = 8000,
) -> str:
    """Formatted evidence block for LLM judge / guardrails."""
    lines: list[str] = []
    total = 0
    for entry in entries:
        agent = entry.get("agent", "")
        title = str(entry.get("title", "")).strip()
        url = str(entry.get("url", "")).strip()
        snippet = str(entry.get("snippet", "")).strip()
        block = f"[Agent: {agent}] {title}\nURL: {url}\n{snippet}\n"
        if total + len(block) > max_chars:
            break
        lines.append(block)
        total += len(block)
    return "\n".join(lines)


def format_agent_outputs_for_judge(
    state: dict[str, Any],
    agent_output_keys: dict[str, str],
    *,
    max_chars_per_agent: int = 4000,
) -> str:
    """Structured per-agent output block for the evaluation judge."""
    sections: list[str] = []
    for agent_name, output_key in agent_output_keys.items():
        if output_key == "final_report":
            continue
        value = state.get(output_key)
        if value is None or not str(value).strip():
            continue
        body: str
        if isinstance(value, dict):
            body = json.dumps(value, indent=2, ensure_ascii=False)
        elif isinstance(value, str):
            stripped = value.strip()
            try:
                parsed = json.loads(stripped)
                body = (
                    json.dumps(parsed, indent=2, ensure_ascii=False)
                    if isinstance(parsed, dict | list)
                    else stripped
                )
            except (json.JSONDecodeError, TypeError):
                body = stripped
        else:
            body = str(value)
        if len(body) > max_chars_per_agent:
            body = body[:max_chars_per_agent] + "\n... [truncated]"
        bm25_status = state.get(f"{agent_name}_bm25_status")
        verify_status = state.get(verification_status_key(agent_name))
        verify_result = state.get(f"{agent_name}_verification_result")
        meta_lines = []
        if verify_status:
            meta_lines.append(f"verification_status={verify_status}")
        if bm25_status:
            meta_lines.append(f"bm25_status={bm25_status}")
        if verify_result:
            meta_lines.append(f"verification_result={verify_result}")
        meta = "\n".join(meta_lines)
        header = f"### {agent_name} ({output_key})"
        if meta:
            header += f"\n{meta}"
        sections.append(f"{header}\n{body}")
    return "\n\n".join(sections)


def set_verification_state(
    state: Any,
    agent_name: str,
    *,
    status: str,
    unsupported: list[str],
) -> None:
    """Write per-agent verification status (parallel-safe)."""
    state[verification_status_key(agent_name)] = status
    state[verification_unsupported_key(agent_name)] = unsupported
    # Legacy global keys for backward compatibility in single-agent paths
    state["verification_status"] = status
    state["unsupported_claims"] = unsupported


def get_verification_status(state: Any, agent_name: str) -> str | None:
    return state.get(verification_status_key(agent_name)) or state.get(
        "verification_status"
    )


def get_unsupported_claims(state: Any, agent_name: str) -> list[str]:
    scoped = state.get(verification_unsupported_key(agent_name))
    if isinstance(scoped, list):
        return scoped
    legacy = state.get("unsupported_claims")
    return legacy if isinstance(legacy, list) else []
