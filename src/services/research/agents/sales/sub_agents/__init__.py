"""Sales sub-agent constructors."""

from .research import create_research_agents
from .signals import create_signals_orchestrator
from .synthesis import create_synthesis_agents

__all__ = [
    "create_research_agents",
    "create_signals_orchestrator",
    "create_synthesis_agents",
]
