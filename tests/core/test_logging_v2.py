import json
import logging
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.core.logging_config import gcp_json_formatter, setup_logging


def test_gcp_json_formatter(mock_settings):
    record = {
        "level": MagicMock(name="INFO"),
        "time": MagicMock(),
        "message": "test message",
        "file": MagicMock(path="test.py"),
        "line": 10,
        "function": "test_func",
        "extra": {
            "trace_id": "trace-123",
            "span_id": "span-456",
            "trace_sampled": True,
            "user_id": "user-789",
        },
    }
    record["level"].name = "INFO"
    record["time"].isoformat.return_value = "2026-05-07T12:00:00Z"

    result = gcp_json_formatter(record)
    data = json.loads(result)

    assert data["severity"] == "INFO"
    assert data["message"] == "test message"
    assert "logging.googleapis.com/trace" in data
    assert data["logging.googleapis.com/spanId"] == "span-456"
    assert data["logging.googleapis.com/trace_sampled"] is True
    assert data["user_id"] == "user-789"


def test_setup_logging_debug(mock_settings):
    mock_settings.DEBUG = True
    mock_settings.LOG_LEVEL = "DEBUG"
    setup_logging()
    assert logging.getLogger().handlers


def test_setup_logging_prod(mock_settings):
    mock_settings.DEBUG = False
    mock_settings.LOG_LEVEL = "INFO"
    setup_logging()
    assert logging.getLogger().handlers


def test_setup_logging_mirrors_to_file(mock_settings):
    mock_settings.DEBUG = True
    mock_settings.LOG_LEVEL = "DEBUG"
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = Path(tmpdir) / "app.log"
        mock_settings.LOG_FILE = str(log_path)
        try:
            with patch("src.core.logging_config.settings", mock_settings):
                mock_settings.app_log_path = log_path
                setup_logging()
                logging.getLogger("sales_agent").info("mirror me")
                for handler in logging.getLogger().handlers:
                    handler.flush()

            assert log_path.read_text(encoding="utf-8").strip().endswith("mirror me")
        finally:
            # setup_logging() only clears the root handler list, so the
            # FileHandler keeps app.log open. Windows refuses to delete an
            # open file, which would fail the TemporaryDirectory cleanup.
            root = logging.getLogger()
            for handler in list(root.handlers):
                root.removeHandler(handler)
                handler.close()
