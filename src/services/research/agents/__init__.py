"""Agent composition layer for research runtime."""

__all__ = [
    "SalesAgentAppFactory",
    "PlanReActAgentFactory",
    "AgentRegistry",
    "PlanAgentSpec",
    "create_sales_agent_app",
]


def __getattr__(name: str):
    if name == "SalesAgentAppFactory":
        from .app_factory import SalesAgentAppFactory

        return SalesAgentAppFactory
    if name == "PlanReActAgentFactory":
        from .factories import PlanReActAgentFactory

        return PlanReActAgentFactory
    if name in {"AgentRegistry", "PlanAgentSpec"}:
        from .registry import AgentRegistry, PlanAgentSpec

        return {"AgentRegistry": AgentRegistry, "PlanAgentSpec": PlanAgentSpec}[name]
    if name == "create_sales_agent_app":
        from .sales import create_sales_agent_app

        return create_sales_agent_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
