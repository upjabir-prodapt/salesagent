"""Agent factories for sales agents."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.planners import BasePlanner

from src.shared.logging_config import logger
from src.worker.model import llm, retry_config

from .callbacks import (
    after_agent_callback,
    after_model_callback,
    after_tool_callback,
    before_agent_callback,
    before_model_callback,
    before_tool_callback,
)
from .retrying_agent import RetryingLlmAgent
from .safety import get_safety_config_for_agent


def create_llm_agent(
    name: str,
    instruction: str,
    description: str | None = None,
    tools: list | None = None,
    output_key: str | None = None,
    output_schema: type | None = None,
    include_contents: str = "none",
    planner: BasePlanner | None = None,
    model: str | None = None,
) -> LlmAgent:
    """Create a structured LLM agent with safety guardrails and standard callbacks."""
    if output_key is None:
        output_key = f"{name.lower()}_output"

    safety_config = get_safety_config_for_agent(name)
    model_instance = Gemini(model=model, retry_options=retry_config) if model else llm

    agent = RetryingLlmAgent(
        name=name,
        model=model_instance,
        instruction=instruction,
        tools=tools or [],
        output_key=output_key,
        output_schema=output_schema,
        include_contents=include_contents,
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


__all__ = ["create_llm_agent"]
