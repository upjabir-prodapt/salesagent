"""
Sub-Agents Package

Contains specialized AI agents for different research domains:
- Research Agents: Firmographics, Geographic, Executive, Strategy, Compliance, Market, Ecosystem, Tech, Procurement
- Signals Agents: Growth, Risk, Campaign signals detection
- Synthesis Agents: Alignment analysis and report compilation
"""

from .research_agents import create_research_agents
from .signals_agent import create_signals_orchestrator
from .synthesis_agents import create_synthesis_agents

__all__ = [
    "create_research_agents",
    "create_signals_orchestrator",
    "create_synthesis_agents",
]
