from unittest.mock import MagicMock, patch

import pytest

from src.agents.salesAgent.agent import create_sales_agent_app


@pytest.fixture
def mock_sub_agents():
    return (
        MagicMock(name="FirmographicsGeographicAgent"),
        MagicMock(name="ExecutiveAgent"),
        MagicMock(name="StrategyComplianceAgent"),
        MagicMock(name="MarketEcosystemAgent"),
        MagicMock(name="TechStackAgent"),
    )


@pytest.fixture
def mock_signals_agent():
    return MagicMock(name="SignalsOrchestrator")


@pytest.fixture
def mock_synthesis_agents():
    return (
        MagicMock(name="AlignmentAnalyst"),
        MagicMock(name="ReportCompiler"),
    )


def test_create_sales_agent_app_structure(
    mock_settings, mock_sub_agents, mock_signals_agent, mock_synthesis_agents
):
    with (
        patch(
            "src.agents.salesAgent.agent.create_research_agents",
            return_value=mock_sub_agents,
        ),
        patch(
            "src.agents.salesAgent.agent.create_signals_orchestrator",
            return_value=mock_signals_agent,
        ),
        patch(
            "src.agents.salesAgent.agent.create_synthesis_agents",
            return_value=mock_synthesis_agents,
        ),
        patch("src.agents.salesAgent.agent.ParallelAgent") as MockParallel,
        patch("src.agents.salesAgent.agent.SequentialAgent") as MockSequential,
        patch("src.agents.salesAgent.agent.App") as MockApp,
    ):
        # Initialize Mocks to track sub_agents
        research_orch_mock = MagicMock(name="ResearchOrchestrator")
        sales_agent_mock = MagicMock(name="SalesResearchAgent")

        # We need to handle the constructor calls.
        # ParallelAgent is called once for ResearchOrchestrator
        # SequentialAgent is called once for SalesResearchAgent

        def mock_sequential_init(name, sub_agents, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            m.sub_agents = sub_agents
            return m

        def mock_parallel_init(name, sub_agents, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            m.sub_agents = sub_agents
            return m

        MockParallel.side_effect = mock_parallel_init
        MockSequential.side_effect = mock_sequential_init

        app = create_sales_agent_app()

        # Verify ParallelAgent (ResearchOrchestrator) construction
        MockParallel.assert_called_once()
        parallel_args = MockParallel.call_args[1]
        assert parallel_args["name"] == "ResearchOrchestrator"
        assert len(parallel_args["sub_agents"]) == 6
        assert mock_signals_agent in parallel_args["sub_agents"]
        for agent in mock_sub_agents:
            assert agent in parallel_args["sub_agents"]

        # Verify SequentialAgent (SalesResearchAgent) construction
        MockSequential.assert_called_once()
        seq_args = MockSequential.call_args[1]
        assert seq_args["name"] == "SalesResearchAgent"
        assert len(seq_args["sub_agents"]) == 3
        # The first sub-agent of SalesResearchAgent should be the ResearchOrchestrator
        research_orch = seq_args["sub_agents"][0]
        assert research_orch.name == "ResearchOrchestrator"
        assert research_orch.sub_agents == parallel_args["sub_agents"]

        assert seq_args["sub_agents"][1] == mock_synthesis_agents[0]  # AlignmentAnalyst
        assert seq_args["sub_agents"][2] == mock_synthesis_agents[1]  # ReportCompiler

        # Verify App construction
        MockApp.assert_called_once()
        app_args = MockApp.call_args[1]
        assert app_args["name"] == "sales_research_app"
        assert app_args["root_agent"].name == "SalesResearchAgent"
        assert app_args["resumability_config"].is_resumable is True
