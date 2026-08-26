"""Persistence of the 12 per-domain research outputs into session state.

Three independent write paths feed the same helpers here, so a single failure
mode can no longer wipe the whole research phase:

1. ``save_domain_output`` — a tool the ResearchSynthesizer calls once per
   domain. Each call is its own function-response event with its own state
   delta, so a later domain running out of output tokens cannot destroy the
   domains already saved. This is the primary path.
2. ``recover_domain_outputs`` on the model response — parses a JSON
   FINAL_ANSWER/AGGREGATED_ANSWER blob when the model emits one anyway.
3. ``recover_domain_outputs`` over the agent's session events at after_agent
   time — last-chance sweep before the gate decides whether to abort.

Paths 2 and 3 never overwrite an already-populated key, so the tool wins.
"""

from __future__ import annotations

import json
from typing import Any

from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

from src.shared.logging_config import logger
from src.worker.domain.contracts import DOMAIN_OUTPUT_KEYS

SAVE_DOMAIN_OUTPUT_TOOL = "save_domain_output"

# Minimum useful payload for a domain. Below this the model is echoing a
# placeholder ("N/A", "{}") rather than research findings.
MIN_DOMAIN_CHARS = 40

# Tolerated spellings for each canonical key. The model routinely drops the
# "agent" infix or the "_output" suffix; rejecting those wastes a whole domain
# over a naming nit.
_KEY_ALIASES: dict[str, str] = {}
for _key in DOMAIN_OUTPUT_KEYS:
    _base = _key[: -len("_output")]
    for _alias in (
        _key,
        _base,
        _base.replace("agent", ""),
        _base.replace("agent", "") + "_output",
        _base.replace("signals", "_signals"),
        _base.replace("signals", "_signals") + "_output",
    ):
        _KEY_ALIASES[_alias.strip("_").lower()] = _key


def canonical_domain_key(name: str) -> str | None:
    """Map a model-supplied domain name onto a canonical DOMAIN_OUTPUT_KEY."""
    if not name:
        return None
    normalized = str(name).strip().strip('"').replace("-", "_").replace(" ", "_")
    return _KEY_ALIASES.get(normalized.strip("_").lower())


def coerce_domain_value(value: Any) -> str:
    """Render a domain payload as the string form downstream agents expect."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


def missing_domain_keys(state: Any) -> list[str]:
    """Domain keys still absent or blank in *state*."""
    return [key for key in DOMAIN_OUTPUT_KEYS if not str(state.get(key) or "").strip()]


def write_domain_output(
    state: Any,
    key: str,
    value: Any,
    *,
    source: str,
    overwrite: bool = False,
) -> bool:
    """Write one domain payload into *state*. Returns True when stored."""
    canonical = canonical_domain_key(key)
    if canonical is None:
        logger.warning(f"[DomainOutput] Ignoring unknown domain key {key!r} ({source})")
        return False

    text = coerce_domain_value(value).strip()
    if not text:
        logger.warning(f"[DomainOutput] Empty payload for {canonical} ({source})")
        return False

    existing = str(state.get(canonical) or "").strip()
    # A longer payload from a later pass is still an upgrade over a stub.
    if existing and not overwrite and len(text) <= len(existing):
        logger.debug(
            f"[DomainOutput] Keeping existing {canonical} "
            f"({len(existing)} chars) over {source} ({len(text)} chars)"
        )
        return False

    state[canonical] = text
    logger.info(
        f"[DomainOutput] Stored {canonical} ({len(text)} chars, source={source})"
    )
    return True


# --- Tool path ------------------------------------------------------------


def save_domain_output(
    domain_key: str, content: str, tool_context: ToolContext
) -> dict[str, Any]:
    """Save one research domain's findings so downstream agents can read them.

    Call this once per domain, immediately after you have researched it. Saving
    incrementally is required: a domain that is not saved through this tool is
    treated as missing and can abort the job.

    Args:
        domain_key: One of the 12 canonical domain keys, e.g.
            "firmographicsagent_output".
        content: The domain's findings. Pass a JSON object serialized as a
            string, or plain structured text. Must be real research content.

    Returns:
        Status of the save plus the domain keys still outstanding.
    """
    canonical = canonical_domain_key(domain_key)
    if canonical is None:
        return {
            "status": "ERROR",
            "message": (
                f"Unknown domain_key {domain_key!r}. Must be one of: "
                f"{', '.join(DOMAIN_OUTPUT_KEYS)}"
            ),
            "remaining": missing_domain_keys(tool_context.state),
        }

    text = coerce_domain_value(content).strip()
    if len(text) < MIN_DOMAIN_CHARS:
        return {
            "status": "ERROR",
            "message": (
                f"Content for {canonical} is too short ({len(text)} chars, minimum "
                f"{MIN_DOMAIN_CHARS}). Search for this domain and save real findings."
            ),
            "remaining": missing_domain_keys(tool_context.state),
        }

    write_domain_output(
        tool_context.state, canonical, text, source="tool", overwrite=True
    )
    remaining = missing_domain_keys(tool_context.state)
    saved = len(DOMAIN_OUTPUT_KEYS) - len(remaining)
    return {
        "status": "SAVED",
        "domain_key": canonical,
        "chars": len(text),
        "saved_count": saved,
        "total": len(DOMAIN_OUTPUT_KEYS),
        "remaining": remaining,
        "message": (
            f"Saved {canonical}. {saved}/{len(DOMAIN_OUTPUT_KEYS)} domains stored."
            + (
                f" Still to save: {', '.join(remaining)}."
                if remaining
                else " All domains stored — you may emit the final answer."
            )
        ),
    }


save_domain_output_tool = FunctionTool(save_domain_output)


# --- Text-recovery path ---------------------------------------------------


def _strip_code_fence(text: str) -> str:
    """Return the contents of the first ``` fenced block, or *text* unchanged."""
    if "```json" in text:
        return text.split("```json", 1)[1].split("```", 1)[0]
    if "```" in text:
        parts = text.split("```")
        if len(parts) >= 3:
            return parts[1]
    return text


def _parse_whole_object(body: str) -> dict[str, str]:
    """Domain payloads from a body that parses as one well-formed JSON object."""
    start = body.find("{")
    end = body.rfind("}") + 1
    if start < 0 or end <= start:
        return {}
    try:
        candidate = json.loads(body[start:end])
    except ValueError:
        return {}
    if not isinstance(candidate, dict):
        return {}

    found: dict[str, str] = {}
    for raw_key, raw_value in candidate.items():
        canonical = canonical_domain_key(raw_key)
        rendered = coerce_domain_value(raw_value).strip()
        if canonical and rendered:
            found[canonical] = rendered
    return found


def _decode_value_after(body: str, marker: str) -> str | None:
    """Decode the single JSON value that follows ``"marker":`` in *body*."""
    idx = body.find(marker)
    if idx < 0:
        return None
    colon = body.find(":", idx + len(marker))
    if colon < 0:
        return None
    rest = body[colon + 1 :].lstrip()
    if not rest:
        return None
    try:
        # raw_decode reads exactly one JSON value starting at position 0 and
        # ignores whatever follows, so a broken sibling domain cannot take
        # this one down with it.
        value, _ = json.JSONDecoder().raw_decode(rest)
    except ValueError:
        return None  # value itself is truncated mid-domain
    return coerce_domain_value(value).strip() or None


def _scan_for_keys(body: str, already_found: dict[str, str]) -> dict[str, str]:
    """Per-key salvage for a body too malformed to parse as a whole."""
    found: dict[str, str] = {}
    for alias, canonical in _KEY_ALIASES.items():
        # Only "*_output" spellings are scanned for. A bare alias like
        # "market" would happily match a nested field inside another domain's
        # payload and fill the wrong key.
        if canonical in already_found or canonical in found:
            continue
        if not alias.endswith("_output"):
            continue
        for marker in (f'"{canonical}"', f'"{alias}"'):
            rendered = _decode_value_after(body, marker)
            if rendered:
                found[canonical] = rendered
                break
    return found


def extract_domain_payloads(text: str) -> dict[str, str]:
    """Best-effort extraction of domain payloads from arbitrary model text.

    Tries a strict whole-object parse first, then falls back to scanning for
    each key and decoding exactly its value. The fallback matters because the
    synthesizer may emit one large object: a single unbalanced brace, or a
    response truncated at the output-token cap, makes ``json.loads`` fail for
    the *whole* object and would otherwise discard every good domain with it.
    """
    if not text or not text.strip():
        return {}

    body = _strip_code_fence(text)
    found = _parse_whole_object(body)
    found.update(_scan_for_keys(body, found))
    return found


def recover_domain_outputs(state: Any, text: str, *, source: str) -> list[str]:
    """Extract domain payloads from *text* and store the ones still missing."""
    payloads = extract_domain_payloads(text)
    if not payloads:
        return []
    stored = [
        key
        for key, value in payloads.items()
        if write_domain_output(state, key, value, source=source)
    ]
    if stored:
        logger.info(
            f"[DomainOutput] Recovered {len(stored)} domain key(s) from {source}: "
            f"{', '.join(stored)}"
        )
    return stored


def log_domain_progress(state: Any, *, stage: str) -> int:
    """Log how many domains are populated. Returns the populated count."""
    remaining = missing_domain_keys(state)
    populated = len(DOMAIN_OUTPUT_KEYS) - len(remaining)
    logger.info(
        f"[DomainOutput] {stage}: {populated}/{len(DOMAIN_OUTPUT_KEYS)} domain "
        "output keys populated"
        + (f"; missing: {', '.join(remaining)}" if remaining else "")
    )
    return populated


__all__ = [
    "DOMAIN_OUTPUT_KEYS",
    "MIN_DOMAIN_CHARS",
    "SAVE_DOMAIN_OUTPUT_TOOL",
    "canonical_domain_key",
    "coerce_domain_value",
    "extract_domain_payloads",
    "log_domain_progress",
    "missing_domain_keys",
    "recover_domain_outputs",
    "save_domain_output",
    "save_domain_output_tool",
    "write_domain_output",
]
