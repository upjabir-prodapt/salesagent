"""Compatibility entrypoint for building the SalesResearch ADK app."""

from ...agents.app_factory import SalesAgentAppFactory


def create_sales_agent_app():
    """Build the ADK app through the centralized application factory."""
    return SalesAgentAppFactory().create()
