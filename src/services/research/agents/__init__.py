"""Agent composition layer for research runtime."""

from .app_factory import SalesAgentAppFactory
from .factories import PlanReActAgentFactory
from .registry import AgentRegistry, PlanAgentSpec

__all__ = [
    "SalesAgentAppFactory",
    "PlanReActAgentFactory",
    "AgentRegistry",
    "PlanAgentSpec",
]
