"""Signals leaves grouped under SignalsOrchestrator."""

from ..factory import PlanReActAgentFactory


def create_signals_orchestrator():
    """Create SignalsOrchestrator with fresh PlanReAct signal agents."""
    return PlanReActAgentFactory.build_signals_orchestrator()
