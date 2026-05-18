from unittest.mock import MagicMock, patch

from src.agents.salesAgent.sub_agents.synthesis_agents import create_synthesis_agents


def test_create_synthesis_agents():
    with patch(
        "src.agents.salesAgent.sub_agents.synthesis_agents.create_llm_agent"
    ) as mock_factory:

        def mock_agent(name, *args, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            return m

        mock_factory.side_effect = mock_agent
        alignment, compiler = create_synthesis_agents()
        assert alignment.name == "AlignmentAnalyst"
        assert compiler.name == "ReportCompiler"
