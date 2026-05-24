from unittest.mock import MagicMock, patch

import pytest

from src.services.research.agent.utils.callbacks import (
    after_agent_callback,
    after_model_callback,
    before_agent_callback,
    before_model_callback,
)


@pytest.fixture
def mock_context():
    context = MagicMock()
    context.agent_name = "TestAgent"
    context.invocation_id = "inv_123"
    context.state = {}
    return context


def test_before_model_callback(mock_context):
    request = MagicMock()
    request.config.temperature = 0.7
    before_model_callback(mock_context, request)
    assert mock_context.state["mc_temperature"] == 0.7


def test_after_model_callback(mock_context):
    response = MagicMock()
    response.usage_metadata.prompt_token_count = 100
    response.usage_metadata.candidates_token_count = 50

    after_model_callback(mock_context, response)

    assert mock_context.state["mc_input_tokens"] == 100
    assert mock_context.state["mc_output_tokens"] == 50


@pytest.mark.asyncio
async def test_before_agent_callback(mock_context):
    with patch("src.services.research.agent.utils.callbacks.track_agent_start") as mock_track:
        await before_agent_callback(mock_context)
        mock_track.assert_called_once()


def test_after_agent_callback(mock_context):
    with patch("src.services.research.agent.utils.callbacks.track_agent_end") as mock_track:
        after_agent_callback(mock_context)
        mock_track.assert_called_once()
