"""
Agent factories for sales agents.

- create_llm_agent: standard LlmAgent (synthesis, report compiler)
- create_plan_react_agent: PlanReAct + web search + BM25 verify_draft_answer
"""

from __future__ import annotations

from collections.abc import Callable

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.planners import BasePlanner, PlanReActPlanner

from ......core.config import settings
from ......core.logging_config import logger
from ......core.model import llm, retry_config
from ...adk.callbacks import (
    after_agent_callback,
    after_model_callback,
    after_tool_callback,
    before_agent_callback,
    before_model_callback,
    before_tool_callback,
)
from ...adk.retrying_llm_agent import RetryingLlmAgent
from ...adk.safety import get_safety_config_for_agent
from ..callbacks.plan_react import (
    plan_after_agent,
    plan_after_model,
    plan_after_tool,
    plan_before_model,
    plan_before_tool,
)
from ..tools.search import make_search_agent_tool, verify_draft_answer_tool


def create_llm_agent(
    name: str,
    instruction: str,
    description: str | None = None,
    tools: list | None = None,
    output_key: str | None = None,
    planner: BasePlanner | None = None,
) -> LlmAgent:
    """Create an LLM agent with standard configuration, safety guardrails, and ADK callbacks."""
    if output_key is None:
        output_key = f"{name.lower()}_output"

    safety_config = get_safety_config_for_agent(name)

    agent = RetryingLlmAgent(
        name=name,
        model=llm,
        instruction=instruction,
        tools=tools or [],
        output_key=output_key,
        description=description or f"Agent for {name}",
        generate_content_config=safety_config,
        planner=planner,
        before_model_callback=before_model_callback,
        after_model_callback=after_model_callback,
        before_agent_callback=before_agent_callback,
        after_agent_callback=after_agent_callback,
        before_tool_callback=before_tool_callback,
        after_tool_callback=after_tool_callback,
    )
    logger.debug(
        f"Created agent: {name} (output_key={output_key}) with safety guardrails and callbacks"
    )
    return agent


def create_plan_react_agent(
    name: str,
    instruction: str,
    description: str | None = None,
    output_key: str | None = None,
    *,
    include_web_search: bool = True,
    include_bm25_verify: bool = True,
    extra_tools: list | None = None,
    instruction_builder: Callable[[str], str] | None = None,
    model: str | None = None,
) -> LlmAgent:
    """PlanReAct LlmAgent with optional web search, BM25 verify, and extra tools."""
    if output_key is None:
        output_key = f"{name.lower()}_output"
    if model is None:
        model = settings.GEMINI_MODEL

    tools: list = []
    if include_web_search:
        tools.append(make_search_agent_tool())
    if extra_tools:
        tools.extend(extra_tools)
    if include_bm25_verify:
        tools.append(verify_draft_answer_tool)

    final_instruction = (
        instruction_builder(instruction) if instruction_builder else instruction
    )

    agent = RetryingLlmAgent(
        name=name,
        model=Gemini(model=model, retry_options=retry_config),
        instruction=final_instruction,
        tools=tools,
        output_key=output_key,
        description=description or f"Research agent for {name}",
        generate_content_config=get_safety_config_for_agent(
            name, max_output_tokens=settings.AGENT_MAX_OUTPUT_TOKENS
        ),
        planner=PlanReActPlanner(),
        before_model_callback=[plan_before_model, before_model_callback],
        after_model_callback=[plan_after_model, after_model_callback],
        before_tool_callback=[plan_before_tool, before_tool_callback],
        after_tool_callback=[plan_after_tool, after_tool_callback],
        before_agent_callback=before_agent_callback,
        after_agent_callback=[plan_after_agent, after_agent_callback],
    )
    tool_names = [getattr(t, "name", type(t).__name__) for t in tools]
    logger.debug(
        f"Created PlanReAct agent: {name} (output_key={output_key}) tools={tool_names}"
    )
    return agent
