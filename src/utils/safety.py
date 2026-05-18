"""
Safety Utilities for Google GenAI Guardrails.

This module provides centralized safety configuration and helper functions
for managing content safety across all AI agents in the Colt-AI system.
"""

from typing import Any

from google.genai import types

from ..core.logging_config import logger


def get_default_safety_settings() -> list[types.SafetySetting]:
    """
    Get default safety settings based on configuration.

    Returns standard safety settings suitable for business research context.
    Default thresholds are moderate - blocking medium and above for most
    categories, but only high for dangerous content to avoid false positives
    in competitive analysis and risk assessment.

    Returns:
        List of SafetySetting objects for use in GenerateContentConfig
    """
    from ..core.config import settings

    safety_settings = [
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

    logger.debug("Generated safety settings from configuration")
    return safety_settings


def get_business_research_safety_settings() -> list[types.SafetySetting]:
    """
    Get safety settings specifically tuned for business research context.

    These settings are more permissive for dangerous content to avoid
    false positives when analyzing competitive threats, risk factors,
    or industry challenges that might be flagged as "dangerous."

    Returns:
        List of SafetySetting objects optimized for business research
    """
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
            # Relaxed for business research - competitive analysis shouldn't be blocked
            threshold=types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
        ),
    ]


def format_safety_ratings(ratings: list[Any]) -> str:
    """
    Format safety ratings for human-readable logging.

    Args:
        ratings: List of SafetyRating objects from the API response

    Returns:
        Formatted string with category and probability for each rating
    """
    if not ratings:
        return "No safety ratings available"

    formatted = []
    for rating in ratings:
        category = str(rating.category).replace("HARM_CATEGORY_", "")
        probability = str(rating.probability)
        blocked = getattr(rating, "blocked", False)
        status = " [BLOCKED]" if blocked else ""
        formatted.append(f"  - {category}: {probability}{status}")

    return "\n".join(formatted)


def analyze_safety_block(
    candidate: Any, request_id: str | None = None
) -> dict[str, Any]:
    """
    Analyze why content was blocked for safety reasons.

    Args:
        candidate: Candidate object from API response
        request_id: Optional request ID for tracking

    Returns:
        Dictionary with analysis of the safety block including:
        - blocked: Whether content was blocked
        - reason: Finish reason
        - categories: List of harm categories that triggered
        - highest_probability: Highest probability rating
        - request_id: Request ID for tracking
    """
    analysis = {
        "blocked": candidate.finish_reason == "SAFETY",
        "reason": candidate.finish_reason,
        "categories": [],
        "highest_probability": "NEGLIGIBLE",
        "request_id": request_id,
        "ratings": [],
    }

    if not hasattr(candidate, "safety_ratings") or not candidate.safety_ratings:
        return analysis

    # Probability ranking for comparison
    prob_rank = {"NEGLIGIBLE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    max_prob_rank = 0

    for rating in candidate.safety_ratings:
        category = str(rating.category).replace("HARM_CATEGORY_", "")
        probability = str(rating.probability)
        blocked = getattr(rating, "blocked", False)

        analysis["ratings"].append(
            {
                "category": category,
                "probability": probability,
                "blocked": blocked,
            }
        )

        if blocked:
            analysis["categories"].append(category)

        # Track highest probability
        if probability in prob_rank and prob_rank[probability] > max_prob_rank:
            max_prob_rank = prob_rank[probability]
            analysis["highest_probability"] = probability

    return analysis


def is_safety_block(event: Any) -> bool:
    """
    Check if an event represents a safety block.

    Args:
        event: Event object from ADK runner

    Returns:
        True if the event indicates content was blocked for safety
    """
    if not hasattr(event, "candidates"):
        return False

    for candidate in event.candidates:
        if hasattr(candidate, "finish_reason") and candidate.finish_reason == "SAFETY":
            return True

    return False


def log_safety_event(
    event_type: str,
    details: dict[str, Any],
    level: str = "WARNING",
) -> None:
    """
    Log a safety-related event with structured data.

    Args:
        event_type: Type of safety event (e.g., "BLOCK", "THRESHOLD_EXCEEDED")
        details: Dictionary with event details
        level: Log level (DEBUG, INFO, WARNING, ERROR)
    """
    from ..core.config import settings

    if not settings.SAFETY_LOGGING_ENABLED:
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
    """
    Create a summary of safety events for reporting.

    Args:
        safety_events: List of safety event dictionaries

    Returns:
        Summary dictionary with counts, categories, and statistics
    """
    summary = {
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

        # Count categories
        for rating in event.get("ratings", []):
            category = rating.get("category", "UNKNOWN")
            summary["categories_triggered"][category] = (
                summary["categories_triggered"].get(category, 0) + 1
            )

            # Track highest severity
            prob = rating.get("probability", "NEGLIGIBLE")
            if prob in prob_rank and prob_rank[prob] > max_severity_rank:
                max_severity_rank = prob_rank[prob]
                summary["highest_severity"] = prob

    return summary


def get_safety_config_for_agent(agent_name: str) -> types.GenerateContentConfig:
    """
    Get GenerateContentConfig with safety settings for a specific agent.

    Args:
        agent_name: Name of the agent (for logging purposes)

    Returns:
        GenerateContentConfig with appropriate safety settings
    """
    safety_settings = get_default_safety_settings()
    logger.debug(f"Created safety config for agent: {agent_name}")

    return types.GenerateContentConfig(
        safety_settings=safety_settings,
    )
