import json
import logging
from unittest.mock import MagicMock

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
