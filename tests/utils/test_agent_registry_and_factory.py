from src.services.research.agents.sales import create_sales_agent_app
from src.services.research.agents.sales.registry import AgentRegistry


def test_agent_registry_has_expected_leaf_counts() -> None:
    assert len(AgentRegistry.research_specs()) == 9
    assert len(AgentRegistry.signal_specs()) == 3


def test_sales_agent_app_factory_builds_expected_root_agent() -> None:
    app = create_sales_agent_app()

    assert app.name == "sales_research_app"
    assert app.root_agent.name == "SalesResearchAgent"
