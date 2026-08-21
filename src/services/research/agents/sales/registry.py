"""Central registry of agent specifications.

DEPRECATED: This module is deprecated in favor of the unified QueryGeneratorAgent.
The 12 research agents and 3 signal agents have been consolidated into a single
query generator that produces BM25-ranked search queries.

Kept for backward compatibility only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanAgentSpec:
    """Declarative agent specification."""

    name: str
    prompt: str
    description: str


class AgentRegistry:
    """DEPRECATED: Agent registry no longer used.

    The 12 research agents have been replaced by a unified QueryGeneratorAgent.
    See src/services/research/agents/sales/query_generator/ for the new architecture.
    """

    @staticmethod
    def research_specs() -> tuple[PlanAgentSpec, ...]:
        """DEPRECATED: Research specs no longer used."""
        return ()

    @staticmethod
    def signal_specs() -> tuple[PlanAgentSpec, ...]:
        """DEPRECATED: Signal specs no longer used."""
        return ()
