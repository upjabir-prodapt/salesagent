"""Reusable PlanReAct sub-agent specifications and builders."""

from __future__ import annotations

from ....agents.factories import PlanReActAgentFactory
from ....agents.registry import PlanAgentSpec


def build_plan_react_agents(specs: list[PlanAgentSpec] | tuple[PlanAgentSpec, ...]) -> dict[str, object]:
    """Instantiate PlanReAct agents keyed by agent name."""
    return PlanReActAgentFactory.build_agents(tuple(specs))

