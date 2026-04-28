import pytest
from unittest.mock import MagicMock, patch
from src.utils.telemetry import track_agent_start, track_agent_end

@pytest.fixture
def mock_context():
    ctx = MagicMock()
    ctx.agent_name = "FirmographicsAgent"
    ctx.invocation_id = "inv_123"
    ctx.state = {}
    return ctx

def test_track_agent_start(mock_context):
    track_agent_start(mock_context)
    assert "at_start_FirmographicsAgent" in mock_context.state

def test_track_agent_end(mock_context):
    track_agent_start(mock_context)
    track_agent_end(mock_context)
    assert "agent_telemetry_records" in mock_context.state
    assert len(mock_context.state["agent_telemetry_records"]) == 1
