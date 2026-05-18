from unittest.mock import MagicMock, patch

from src.utils.agent import _truncate, log_event


def test_truncate():
    assert _truncate("hello world", 5) == "hello..."
    assert _truncate("hi", 10) == "hi"


def test_log_event_text():
    with patch("src.utils.agent.logger") as mock_logger:
        event = MagicMock()
        event.author = "TestAgent"
        part = MagicMock()
        part.text = "Hello"
        event.content.parts = [part]

        log_event(event)
        # It accumulates and flushes at the end
        mock_logger.info.assert_called()


def test_log_event_tool_call():
    with patch("src.utils.agent.logger") as mock_logger:
        event = MagicMock()
        event.author = "TestAgent"
        part = MagicMock()
        part.text = None
        # ADK uses function_call
        part.function_call = MagicMock()
        part.function_call.name = "search"
        part.function_call.args = {"q": "test"}

        event.content.parts = [part]

        # log_event only logs tool calls if verbose=True (default)
        log_event(event, verbose=True)

        # Verify it was called with our "Tool call:" string from the fixed code
        mock_logger.info.assert_called()
        found = False
        for call in mock_logger.info.call_args_list:
            if "[Tool Call:" in str(call.args[0]):
                found = True
                break
        assert found
