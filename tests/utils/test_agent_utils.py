from pathlib import Path
from unittest.mock import MagicMock, patch

from src.services.research.agent.utils.agent import _truncate, log_event


def test_truncate():
    assert _truncate("hello world", 5) == "hello..."
    assert _truncate("hi", 10) == "hi"


def test_log_event_text():
    with patch("src.services.research.agent.utils.agent.logger") as mock_logger:
        event = MagicMock()
        event.author = "TestAgent"
        part = MagicMock()
        part.text = "Hello"
        event.content.parts = [part]

        log_event(event)
        mock_logger.info.assert_called_once_with("TestAgent > Hello")


def test_log_event_tool_call_verbose():
    with patch("src.services.research.agent.utils.agent.logger") as mock_logger:
        event = MagicMock()
        event.author = "TestAgent"
        part = MagicMock()
        part.text = None
        part.function_call = MagicMock()
        part.function_call.name = "search"
        part.function_call.args = {"q": "test"}
        event.content.parts = [part]

        log_event(event, verbose=True)

        assert mock_logger.info.call_count == 1
        assert "[Tool Call: search" in mock_logger.info.call_args.args[0]


def test_log_event_logger_and_file(tmp_path: Path):
    log_path = tmp_path / "events.log"
    with patch("src.services.research.agent.utils.agent.logger"):
        event = MagicMock()
        event.author = "TestAgent"
        part = MagicMock()
        part.text = "Hello file"
        event.content.parts = [part]

        log_event(event, verbose=True, log_file=log_path)

    assert log_path.read_text(encoding="utf-8") == "TestAgent > Hello file\n"
