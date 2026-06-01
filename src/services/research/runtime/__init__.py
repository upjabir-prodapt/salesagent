"""ADK runtime services and utilities for research execution."""

__all__ = ["ResearchRunnerService"]


def __getattr__(name: str):
    if name == "ResearchRunnerService":
        from .runner import ResearchRunnerService

        return ResearchRunnerService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
