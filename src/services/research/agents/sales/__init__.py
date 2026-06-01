"""Sales agent composition package for research runtime."""

from .app_factory import SalesAgentAppFactory


def create_sales_agent_app():
    """Build the ADK app through the centralized application factory."""
    return SalesAgentAppFactory().create()


__all__ = ["SalesAgentAppFactory", "create_sales_agent_app"]
