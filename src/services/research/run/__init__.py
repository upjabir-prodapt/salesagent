"""ADK runner execution for research jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .runner import ResearchRunnerService

__all__ = ["ResearchRunnerService"]


def __getattr__(name: str):
    if name == "ResearchRunnerService":
        from .runner import ResearchRunnerService

        return ResearchRunnerService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
