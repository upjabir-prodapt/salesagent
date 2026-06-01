"""Backward-compatible import path for PlanReAct spec builders."""

from ....agents.registry import PlanAgentSpec
from ....agents.sales.factory import PlanReActAgentFactory


def build_plan_react_agents(
    specs: list[PlanAgentSpec] | tuple[PlanAgentSpec, ...],
) -> dict[str, object]:
    """Instantiate PlanReAct agents keyed by agent name."""
    return PlanReActAgentFactory.build_agents(tuple(specs))

