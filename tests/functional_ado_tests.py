from unittest.mock import MagicMock, patch

import pytest

from src.agents.salesAgent.prompts import EXECUTIVE_PROMPT, FIRMOGRAPHICS_PROMPT
from src.agents.salesAgent.sub_agents.research_agents import create_research_agents


@pytest.fixture
def mock_agents():
    # We want to test the logic of the agents, not the LLM itself
    with (
        patch(
            "src.agents.salesAgent.sub_agents.research_agents.create_llm_agent"
        ) as mock_factory,
        patch(
            "src.agents.salesAgent.sub_agents.research_agents.SequentialAgent"
        ) as mock_sequential,
    ):

        def mock_agent_side_effect(name, prompt, description, tools=None):
            m = MagicMock(name=name)
            m.name = name
            m.prompt = prompt
            return m

        mock_factory.side_effect = mock_agent_side_effect

        # Mock SequentialAgent to just be a container for the first sub_agent for simplicity in tests
        def mock_seq_side_effect(name, sub_agents, description):
            m = MagicMock(name=name)
            m.name = name
            m.sub_agents = sub_agents
            # When run, it just returns the first sub_agent's mock output for testing
            m.run = MagicMock(
                side_effect=lambda *args, **kwargs: sub_agents[0].run(*args, **kwargs)
            )
            return m

        mock_sequential.side_effect = mock_seq_side_effect

        return create_research_agents()


def test_firmographics_prompt_contains_unavailable_instruction():
    """
    Test Case: Publicly Unavailable data handling for private company
    Objective: Verify that the Firmographics Agent is instructed to return 'publicly unavailable'
    """
    # We check the prompt itself since the agent's behavior is defined by its system instructions
    assert "publicly unavailable" in FIRMOGRAPHICS_PROMPT.lower()
    assert "no training data" in FIRMOGRAPHICS_PROMPT.lower()
    assert "do not estimate or infer" in FIRMOGRAPHICS_PROMPT.lower()


def test_executive_prompt_contains_unavailable_instruction():
    """
    Test Case: No leadership page found - graceful degradation
    Objective: Verify that the Executive Agent is instructed to handle missing data gracefully
    """
    assert "publicly unavailable" in EXECUTIVE_PROMPT.lower()
    assert "no training data" in EXECUTIVE_PROMPT.lower()


def test_no_bullet_points_instruction_in_guidelines():
    """
    Test Case: No bullet points in narrative sections
    Objective: Verify that the global research guidelines forbid generic statements and prioritize specific formatting
    """
    from src.agents.salesAgent.prompts import RESEARCH_GUIDELINES

    # Although the 'no bullet points' is specifically a Compiler/Synthesis rule,
    # we verify that the agents are pushed towards specific, factual evidence over generic lists.
    assert "no generic industry claims" in RESEARCH_GUIDELINES.lower()


def test_report_compiler_enforces_no_bullets():
    """
    Test Case: No bullet points in narrative sections
    Objective: Verify that the Report Compiler prompt explicitly forbids bullet points in Section 12
    """
    from src.agents.salesAgent.prompts import REPORT_COMPILER_PROMPT

    assert "no bullet points" in REPORT_COMPILER_PROMPT.lower()
    assert "no dashes" in REPORT_COMPILER_PROMPT.lower()
    assert "no numbered lists" in REPORT_COMPILER_PROMPT.lower()
