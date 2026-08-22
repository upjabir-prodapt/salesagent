from src.services.research.agents import create_sales_agent_app
from src.services.research.agents.sales.registry import AgentRegistry


def test_agent_registry_is_deprecated_and_empty() -> None:
    # The 12 research/signal leaf agents were consolidated into the unified
    # QueryGeneratorAgent; AgentRegistry survives only for import compatibility.
    assert AgentRegistry.research_specs() == ()
    assert AgentRegistry.signal_specs() == ()


def test_sales_agent_app_factory_builds_expected_root_agent() -> None:
    app = create_sales_agent_app()

    assert app.name == "sales_research_app"
    assert app.root_agent.name == "SalesResearchAgent"
