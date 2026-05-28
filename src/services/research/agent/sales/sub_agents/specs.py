"""Reusable PlanReAct sub-agent specifications and builders."""

from __future__ import annotations

from dataclasses import dataclass

from ..utils import create_plan_react_agent


@dataclass(frozen=True)
class PlanAgentSpec:
    name: str
    prompt: str
    description: str


def build_plan_react_agents(specs: list[PlanAgentSpec]) -> dict[str, object]:
    """Instantiate PlanReAct agents keyed by agent name."""
    return {
        spec.name: create_plan_react_agent(spec.name, spec.prompt, spec.description)
        for spec in specs
    }

