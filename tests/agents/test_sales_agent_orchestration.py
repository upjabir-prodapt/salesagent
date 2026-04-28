import pytest
from unittest.mock import patch, MagicMock
from src.agents.salesAgent.agent import create_sales_agent_app

def test_create_sales_agent_app(mock_settings):
    with patch("src.agents.salesAgent.agent.App") as mock_app_cls, \
         patch("src.agents.salesAgent.agent.ParallelAgent"), \
         patch("src.agents.salesAgent.agent.SequentialAgent"), \
         patch("src.agents.salesAgent.agent.create_research_agents", return_value=(MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock())), \
         patch("src.agents.salesAgent.agent.create_signals_orchestrator"), \
         patch("src.agents.salesAgent.agent.create_synthesis_agents", return_value=(MagicMock(), MagicMock())), \
         patch("src.agents.salesAgent.agent.create_research_validator"):
        
        app = create_sales_agent_app()
        assert app is not None
        mock_app_cls.assert_called_once()
