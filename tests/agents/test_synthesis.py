import pytest
from unittest.mock import patch, MagicMock
from src.agents.salesAgent.sub_agents.synthesis_agents import create_research_validator, create_synthesis_agents

def test_create_research_validator():
    with patch("src.agents.salesAgent.sub_agents.synthesis_agents.create_llm_agent") as mock_factory:
        m = MagicMock(name="ResearchValidator")
        m.name = "ResearchValidator"
        mock_factory.return_value = m
        agent = create_research_validator()
        assert agent.name == "ResearchValidator"

def test_create_synthesis_agents():
    with patch("src.agents.salesAgent.sub_agents.synthesis_agents.create_llm_agent") as mock_factory:
        def mock_agent(name, *args, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            return m
        mock_factory.side_effect = mock_agent
        alignment, compiler = create_synthesis_agents()
        assert alignment.name == "AlignmentAnalyst"
        assert compiler.name == "ReportCompiler"
