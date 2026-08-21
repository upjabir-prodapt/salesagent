"""Factory for building synthesis agents (alignment analyst and report compiler).

DEPRECATED: The 12 parallel research agents and 3 signal agents have been replaced
by a unified QueryGeneratorAgent. Only synthesis agents (alignment + report) remain.
See src/services/research/agents/sales/query_generator/ for the new architecture.
"""

from __future__ import annotations

from .synthesis import create_synthesis_agents


class PlanReActAgentFactory:
    """Factory for synthesis agents (alignment analyst and report compiler)."""

    @staticmethod
    def build_synthesis_agents(company_name: str = "Unknown"):
        """Create fresh synthesis agent instances for each run.

        Args:
            company_name: Company name for context tool initialization

        Returns:
            Tuple of (AlignmentAnalyst, ReportCompiler) agents
        """
        return create_synthesis_agents(company_name)
