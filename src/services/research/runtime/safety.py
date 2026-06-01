"""Runtime safety facade with patch-friendly compatibility wrappers."""

from __future__ import annotations

from typing import Any

from ..agent.utils import safety as _legacy_safety

types = _legacy_safety.types
logger = _legacy_safety.logger


def _sync_legacy_bindings() -> None:
    _legacy_safety.types = types
    _legacy_safety.logger = logger


def get_default_safety_settings():
    _sync_legacy_bindings()
    return _legacy_safety.get_default_safety_settings()


def get_business_research_safety_settings():
    _sync_legacy_bindings()
    return _legacy_safety.get_business_research_safety_settings()


def format_safety_ratings(ratings: list[Any]) -> str:
    _sync_legacy_bindings()
    return _legacy_safety.format_safety_ratings(ratings)


def analyze_safety_block(candidate: Any, request_id: str | None = None) -> dict[str, Any]:
    _sync_legacy_bindings()
    return _legacy_safety.analyze_safety_block(candidate, request_id)


def is_safety_block(event: Any) -> bool:
    _sync_legacy_bindings()
    return _legacy_safety.is_safety_block(event)


def log_safety_event(event_type: str, details: dict[str, Any], level: str = "WARNING") -> None:
    _sync_legacy_bindings()
    _legacy_safety.log_safety_event(event_type, details, level)


def create_safety_summary(safety_events: list[dict[str, Any]]) -> dict[str, Any]:
    _sync_legacy_bindings()
    return _legacy_safety.create_safety_summary(safety_events)


def get_safety_config_for_agent(agent_name: str):
    _sync_legacy_bindings()
    return _legacy_safety.get_safety_config_for_agent(agent_name)


__all__ = [
    "types",
    "logger",
    "analyze_safety_block",
    "create_safety_summary",
    "format_safety_ratings",
    "get_business_research_safety_settings",
    "get_default_safety_settings",
    "get_safety_config_for_agent",
    "is_safety_block",
    "log_safety_event",
]
