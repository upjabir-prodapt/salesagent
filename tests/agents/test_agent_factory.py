from unittest.mock import patch

from src.services.research.agent.sales.utils.agent_factory import create_llm_agent


def test_create_llm_agent(mock_settings):
    mock_settings.SAFETY_HARASSMENT_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_HATE_SPEECH_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_SEXUAL_THRESHOLD = "BLOCK_LOW_AND_ABOVE"
    mock_settings.SAFETY_DANGEROUS_THRESHOLD = "BLOCK_ONLY_HIGH"

    with patch("src.services.research.agent.sales.utils.agent_factory.LlmAgent") as mock_agent_cls:
        agent = create_llm_agent("TestAgent", "Instruction", "Description")
        mock_agent_cls.assert_called_once()
        assert agent is not None
