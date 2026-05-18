"""
Agent Factory Module

Contains a factory function for creating LlmAgent instances with standard configuration,
safety guardrails, and ADK callbacks.
"""

from google.adk.agents import LlmAgent
from google.adk.planners import BasePlanner

from ....core.logging_config import logger
from ....core.model import llm
from ....utils.callbacks import (
    after_agent_callback,
    after_model_callback,
    after_tool_callback,
    before_agent_callback,
    before_model_callback,
    before_tool_callback,
)
from ....utils.safety import get_safety_config_for_agent


def create_llm_agent(
    name: str,
    instruction: str,
    description: str | None = None,
    tools: list | None = None,
    output_key: str | None = None,
    planner: BasePlanner | None = None,
) -> LlmAgent:
    """Create an LLM agent with standard configuration, safety guardrails, and ADK callbacks.

    Args:
        name: Name of the agent.
        instruction: System instruction/prompt for the agent.
        description: Human-readable description of what the agent does.
        tools: List of tools to provide to the agent.
        output_key: Key to use for the agent's output. Defaults to {name.lower()}_output.
        planner: Optional ADK planner (e.g. PlanReActPlanner) for tool reasoning loops.

    Returns:
        Configured LlmAgent instance with safety settings and callbacks.
    """
    if output_key is None:
        output_key = f"{name.lower()}_output"

    # Get safety configuration
    safety_config = get_safety_config_for_agent(name)

    agent = LlmAgent(
        name=name,
        model=llm,
        instruction=instruction,
        tools=tools or [],
        output_key=output_key,
        description=description or f"Agent for {name}",
        generate_content_config=safety_config,
        planner=planner,
        # ADK Callbacks for comprehensive monitoring
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
