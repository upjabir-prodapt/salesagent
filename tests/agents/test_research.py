from unittest.mock import MagicMock, patch

from src.agents.salesAgent.sub_agents.research_agents import create_research_agents


def test_create_research_agents():
    # Mock create_llm_agent and SequentialAgent to avoid real agent creation and validation errors
    with (
        patch(
            "src.agents.salesAgent.sub_agents.research_agents.create_llm_agent"
        ) as mock_factory,
        patch(
            "src.agents.salesAgent.sub_agents.research_agents.SequentialAgent"
        ) as mock_sequential,
    ):

        def mock_agent(name, *args, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            return m

        mock_factory.side_effect = mock_agent
        mock_sequential.side_effect = lambda name, sub_agents, **kwargs: MagicMock(
            name=name, sub_agents=sub_agents, name_attr=name
        )

        # Update the mock_sequential to have a 'name' attribute
        def create_mock_sequential(name, sub_agents, **kwargs):
            m = MagicMock(name=name)
            m.name = name
            m.sub_agents = sub_agents
            return m

        mock_sequential.side_effect = create_mock_sequential

        agents = create_research_agents()

        assert len(agents) == 5
        # firmographics_geographic_agent
        assert agents[0].name == "FirmographicsGeographicAgent"
        # firmographics + geographic
        assert len(agents[0].sub_agents) == 2
        # executive_agent
        assert agents[1].name == "ExecutivePipeline"
        assert len(agents[1].sub_agents) == 1

        # strategy_compliance_agent
        assert agents[2].name == "StrategyComplianceAgent"
        assert len(agents[2].sub_agents) == 2

        # market_ecosystem_agent
        assert agents[3].name == "MarketEcosystemAgent"
        assert len(agents[3].sub_agents) == 3

        # tech_stack_agent
        assert agents[4].name == "TechStackPipeline"
        assert len(agents[4].sub_agents) == 1
