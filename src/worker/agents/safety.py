"""Safety configuration and utilities for Google GenAI agents."""

from __future__ import annotations

from typing import Any

from google.genai import types

import src.shared.config as core_config
from src.shared.logging_config import logger


def get_default_safety_settings() -> list[types.SafetySetting]:
    """Get default safety settings based on configuration."""
    settings = core_config.settings
    return [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=getattr(
                types.HarmBlockThreshold,
                settings.SAFETY_HARASSMENT_THRESHOLD,
            ),
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=getattr(
                types.HarmBlockThreshold,
                settings.SAFETY_HATE_SPEECH_THRESHOLD,
            ),
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=getattr(
                types.HarmBlockThreshold,
                settings.SAFETY_SEXUAL_THRESHOLD,
            ),
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=getattr(
                types.HarmBlockThreshold,
                settings.SAFETY_DANGEROUS_THRESHOLD,
            ),
        ),
    ]


def get_business_research_safety_settings() -> list[types.SafetySetting]:
    """Get safety settings specifically tuned for business research context."""
    return [
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
        ),
        types.SafetySetting(
            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
    ]


def get_safety_config_for_agent(
    agent_name: str, *, max_output_tokens: int | None = None
) -> types.GenerateContentConfig:
    """Get GenerateContentConfig with safety settings for an agent."""
    safety_settings = get_default_safety_settings()
    return types.GenerateContentConfig(
        safety_settings=safety_settings,
        max_output_tokens=max_output_tokens,
    )


def is_safety_block(event: Any) -> bool:
    """Check if an ADK event was blocked by safety filters."""
    if not hasattr(event, "candidates") or not event.candidates:
        return False
    for candidate in event.candidates:
        if getattr(candidate, "finish_reason", None) == "SAFETY":
            return True
    return False


def log_safety_event(
    event_type: str,
    details: dict[str, Any],
    level: str = "WARNING",
) -> None:
    """Log a safety-related event with structured data."""
    if not getattr(core_config.settings, "SAFETY_LOGGING_ENABLED", True):
        return
    log_message = f"Safety Event [{event_type}]: {details}"
    if level == "DEBUG":
        logger.debug(log_message)
    elif level == "INFO":
        logger.info(log_message)
    elif level == "WARNING":
        logger.warning(log_message)
    elif level == "ERROR":
        logger.error(log_message)
    else:
        logger.info(log_message)


def create_safety_summary(safety_events: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a summary of safety events for reporting."""
    summary: dict[str, Any] = {
        "total_events": len(safety_events),
        "blocked_count": 0,
        "categories_triggered": {},
        "highest_severity": "NEGLIGIBLE",
        "events": safety_events,
    }
    prob_rank = {"NEGLIGIBLE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    max_severity_rank = 0
    for event in safety_events:
        if event.get("blocked", False):
            summary["blocked_count"] += 1
        for rating in event.get("ratings", []):
            category = rating.get("category", "UNKNOWN")
            summary["categories_triggered"][category] = (
                summary["categories_triggered"].get(category, 0) + 1
            )
            prob = rating.get("probability", "NEGLIGIBLE")
            if prob in prob_rank and prob_rank[prob] > max_severity_rank:
                max_severity_rank = prob_rank[prob]
                summary["highest_severity"] = prob
    return summary


def format_safety_ratings(ratings: list[Any]) -> str:
    """Format safety ratings for logging and reporting."""
    if not ratings:
        return "No safety ratings available"
    lines = []
    for rating in ratings:
        cat = str(getattr(rating, "category", "")).replace("HARM_CATEGORY_", "")
        prob = str(getattr(rating, "probability", "UNKNOWN"))
        blocked = getattr(rating, "blocked", False)
        suffix = " [BLOCKED]" if blocked else ""
        lines.append(f"{cat}: {prob}{suffix}")
    return "\n".join(lines)


def analyze_safety_block(
    candidate: Any, request_id: str | None = None
) -> dict[str, Any]:
    """Analyze a safety block event to determine the cause and details."""
    finish_reason = getattr(candidate, "finish_reason", "UNKNOWN")
    blocked = finish_reason == "SAFETY"
    ratings = getattr(candidate, "safety_ratings", None) or []

    categories = []
    highest_prob = "NEGLIGIBLE"
    prob_rank = {"NEGLIGIBLE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

    for r in ratings:
        cat = str(getattr(r, "category", "")).replace("HARM_CATEGORY_", "")
        prob = str(getattr(r, "probability", "NEGLIGIBLE"))
        if getattr(r, "blocked", False) or prob in ("MEDIUM", "HIGH"):
            categories.append(cat)
        if prob_rank.get(prob, 0) > prob_rank.get(highest_prob, 0):
            highest_prob = prob

    analysis: dict[str, Any] = {
        "blocked": blocked,
        "reason": finish_reason,
        "categories": categories,
        "highest_probability": highest_prob,
    }
    if request_id is not None:
        analysis["request_id"] = request_id
    return analysis


__all__ = [
    "get_default_safety_settings",
    "get_safety_config_for_agent",
    "is_safety_block",
    "log_safety_event",
    "create_safety_summary",
    "format_safety_ratings",
    "analyze_safety_block",
]
