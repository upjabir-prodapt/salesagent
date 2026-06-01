from unittest.mock import MagicMock, patch

import pytest

from src.services.research.agents.sales.prompts import (
    EXECUTIVE_PROMPT,
    FIRMOGRAPHICS_PROMPT,
)
from src.services.research.agents.sales.sub_agents.research import create_research_agents


@pytest.fixture
def mock_agents():
    # We want to test the logic of the agents, not the LLM itself
    with (
        patch(
            "src.services.research.agents.sales.factory.create_plan_react_agent"
        ) as mock_factory,
        patch(
            "src.services.research.agents.sales.factory.SequentialAgent"
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
    assert "publicly unavailable" in FIRMOGRAPHICS_PROMPT.lower()
    assert "not memory" in FIRMOGRAPHICS_PROMPT.lower() or "training" in FIRMOGRAPHICS_PROMPT.lower()
    assert "interpolat" in FIRMOGRAPHICS_PROMPT.lower()


def test_executive_prompt_contains_unavailable_instruction():
    """
    Test Case: No leadership page found - graceful degradation
    Objective: Verify that the Executive Agent is instructed to handle missing data gracefully
    """
    assert "publicly unavailable" in EXECUTIVE_PROMPT.lower()
    assert "no training" in EXECUTIVE_PROMPT.lower() or "not memory" in EXECUTIVE_PROMPT.lower()


def test_no_bullet_points_instruction_in_guidelines():
    """
    Test Case: No bullet points in narrative sections
    Objective: Verify that the global research guidelines forbid generic statements and prioritize specific formatting
    """
    from src.services.research.agents.sales.prompts import RESEARCH_GUIDELINES

    # Although the 'no bullet points' is specifically a Compiler/Synthesis rule,
    # we verify that the agents are pushed towards specific, factual evidence over generic lists.
    assert "no generic industry claims" in RESEARCH_GUIDELINES.lower()


def test_report_compiler_enforces_no_bullets():
    """
    Test Case: No bullet points in narrative sections
    Objective: Verify that the Report Compiler prompt explicitly forbids bullet points in Section 12
    """
    from src.services.research.agents.sales.prompts import REPORT_COMPILER_PROMPT

    assert "no bullet points" in REPORT_COMPILER_PROMPT.lower()
    assert "dashes" in REPORT_COMPILER_PROMPT.lower()
    assert "numbered lists" in REPORT_COMPILER_PROMPT.lower()
    assert "{firmographicsagent_output?}" in REPORT_COMPILER_PROMPT
    assert "{alignment_output?}" in REPORT_COMPILER_PROMPT
    assert "{company_name?}" in REPORT_COMPILER_PROMPT
