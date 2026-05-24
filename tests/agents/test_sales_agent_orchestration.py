from unittest.mock import MagicMock, patch

from src.services.research.agent.sales.agent import create_sales_agent_app


def test_create_sales_agent_app(mock_settings):
    with (
        patch("src.services.research.agent.sales.agent.App") as mock_app_cls,
        patch("src.services.research.agent.sales.agent.ParallelAgent"),
        patch("src.services.research.agent.sales.agent.SequentialAgent"),
        patch(
            "src.services.research.agent.sales.agent.create_research_agents",
            return_value=(
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
                MagicMock(),
            ),
        ),
        patch("src.services.research.agent.sales.agent.create_signals_orchestrator"),
        patch(
            "src.services.research.agent.sales.agent.create_synthesis_agents",
            return_value=(MagicMock(), MagicMock()),
        ),
    ):
        app = create_sales_agent_app()
        assert app is not None
        mock_app_cls.assert_called_once()
