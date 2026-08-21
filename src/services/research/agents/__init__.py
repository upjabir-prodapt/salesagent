"""ADK graph construction for sales research."""

from .sales.composition.app import SalesAgentAppFactory


def create_sales_agent_app(company_name: str = "Unknown"):
    """Build the ADK app through the centralized application factory."""
    return SalesAgentAppFactory().create(company_name)


__all__ = ["SalesAgentAppFactory", "create_sales_agent_app"]
