"""Tests for unified job / ADK session id helper."""

from src.services.research.agent.session_ids import runner_session_id


def test_runner_session_id_attempt_zero():
    assert runner_session_id("job_abc-123", 0) == "job_abc-123"


def test_runner_session_id_retry_attempt():
    assert runner_session_id("job_abc-123", 1) == "job_abc-123_retry_1"
    assert runner_session_id("job_abc-123", 2) == "job_abc-123_retry_2"
