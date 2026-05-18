from unittest.mock import MagicMock, patch

from src.agents.salesAgent.sub_agents.signals_agent import create_signals_orchestrator


def test_create_signals_orchestrator():
    with (
        patch(
            "src.agents.salesAgent.sub_agents.signals_agent.create_llm_agent"
        ) as mock_factory,
        patch(
            "src.agents.salesAgent.sub_agents.signals_agent.ParallelAgent"
        ) as mock_parallel,
    ):

        def mock_agent(name, *args, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            return m

        mock_factory.side_effect = mock_agent

        def create_mock_parallel(name, sub_agents, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            m.sub_agents = sub_agents
            return m

        mock_parallel.side_effect = create_mock_parallel

        orchestrator = create_signals_orchestrator()

        assert orchestrator.name == "SignalsOrchestrator"
        assert len(orchestrator.sub_agents) == 3
        sub_agent_names = [sa.name for sa in orchestrator.sub_agents]
        assert "GrowthSignals" in sub_agent_names
        assert "RiskSignals" in sub_agent_names
        assert "CampaignSignals" in sub_agent_names
