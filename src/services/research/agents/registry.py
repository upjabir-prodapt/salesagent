"""Backward-compatible import path for sales registry types."""

from .sales.registry import AgentRegistry, PlanAgentSpec

__all__ = ["AgentRegistry", "PlanAgentSpec"]
