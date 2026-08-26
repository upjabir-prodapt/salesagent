from src.worker.agents import create_sales_agent_app


def test_sales_agent_app_factory_builds_expected_root_agent() -> None:
    app = create_sales_agent_app()

    assert app.name == "sales_research_app"
    assert app.root_agent.name == "SalesResearchAgent"
    assert len(app.root_agent.sub_agents) == 4
