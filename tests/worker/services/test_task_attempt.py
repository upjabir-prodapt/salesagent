"""Unit tests for the Cloud Tasks re-dispatch decision.

The decision is a pure function shared by ResearchJobRunner (which writes
the job status) and ResearchTaskHandler (which chooses the HTTP response),
so that the two cannot disagree about whether a failure is retryable --
disagreement is exactly what broke the queue-level retry before:
_handle_failure wrote FAILED while the route returned 500, and the next
delivery read the FAILED back and no-opped.
"""

from __future__ import annotations

import pytest

from src.worker.agents.base import AgentError, ErrorKind
from src.worker.services import task_attempt as ta
from src.worker.services.task_attempt import (
    RETRY_COUNT_HEADER,
    JobPhase,
    TaskAttempt,
    is_queue_retryable,
    should_redispatch,
)


class TestTaskAttempt:
    def test_retry_count_is_zero_based(self):
        first = TaskAttempt(retry_count=0, max_attempts=5)
        assert first.attempt_number == 1
        assert first.label == "attempt 1 of 5"

    def test_attempts_remaining_until_the_last_delivery(self):
        assert TaskAttempt(0, 5).attempts_remaining is True
        assert TaskAttempt(3, 5).attempts_remaining is True
        # 5th delivery: the queue will make no more.
        assert TaskAttempt(4, 5).attempts_remaining is False
        assert TaskAttempt(9, 5).attempts_remaining is False

    def test_single_attempt_queue_never_has_a_delivery_left(self):
        assert TaskAttempt(0, 1).attempts_remaining is False


class TestFromHeaders:
    def test_reads_the_cloud_tasks_header(self, monkeypatch):
        monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)
        attempt = TaskAttempt.from_headers({RETRY_COUNT_HEADER: "2"})
        assert attempt is not None
        assert attempt.retry_count == 2
        assert attempt.max_attempts == 5

    def test_header_lookup_is_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)
        attempt = TaskAttempt.from_headers({RETRY_COUNT_HEADER.lower(): "1"})
        assert attempt is not None and attempt.retry_count == 1

    def test_absent_header_means_no_queue(self):
        """The local dev path posts straight to the worker with no Cloud
        Tasks headers; there is nothing to re-deliver a failure."""
        assert TaskAttempt.from_headers({}) is None
        assert TaskAttempt.from_headers(None) is None
        assert TaskAttempt.from_headers("not a mapping") is None

    def test_malformed_header_is_ignored(self):
        assert TaskAttempt.from_headers({RETRY_COUNT_HEADER: "abc"}) is None
        assert TaskAttempt.from_headers({RETRY_COUNT_HEADER: None}) is None

    def test_negative_retry_count_is_clamped(self, monkeypatch):
        monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)
        attempt = TaskAttempt.from_headers({RETRY_COUNT_HEADER: "-3"})
        assert attempt is not None and attempt.retry_count == 0

    def test_max_attempts_is_never_below_one(self, monkeypatch):
        monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 0, raising=False)
        attempt = TaskAttempt.from_headers({RETRY_COUNT_HEADER: "0"})
        assert attempt is not None and attempt.max_attempts == 1


class TestIsQueueRetryable:
    @pytest.mark.parametrize(
        "message",
        ["429 RESOURCE_EXHAUSTED", "quota exceeded", "Connection reset by peer"],
    )
    def test_transient_infrastructure_errors_are_retryable(self, message):
        assert is_queue_retryable(Exception(message)) is True

    def test_timeout_is_retryable(self):
        assert is_queue_retryable(TimeoutError("deadline exceeded")) is True

    @pytest.mark.parametrize(
        "message", ["blocked_reason SAFETY", "something nobody has seen"]
    )
    def test_deterministic_failures_are_not_retryable(self, message):
        """Re-running the whole pipeline four more times cannot change a
        blocked response or a programming error."""
        assert is_queue_retryable(Exception(message)) is False

    def test_invalid_output_is_off_by_default(self, monkeypatch):
        monkeypatch.setattr(
            ta.settings, "CLOUD_TASKS_RETRY_ON_INVALID_OUTPUT", False, raising=False
        )
        assert is_queue_retryable(Exception("validation failed: nope")) is False

    def test_invalid_output_can_be_enabled(self, monkeypatch):
        monkeypatch.setattr(
            ta.settings, "CLOUD_TASKS_RETRY_ON_INVALID_OUTPUT", True, raising=False
        )
        assert is_queue_retryable(Exception("validation failed: nope")) is True

    def test_agent_error_kind_is_honoured(self):
        """AgentError carries the kind classify() already decided, so a
        rate-limit exhaustion from Agent.run is recognised as such."""
        rate_limited = AgentError(
            "ReportCompiler failed after 6 attempt(s)",
            kind=ErrorKind.RATE_LIMIT,
            agent_name="ReportCompiler",
        )
        assert is_queue_retryable(rate_limited) is True
        safety = AgentError("blocked", kind=ErrorKind.SAFETY)
        assert is_queue_retryable(safety) is False


class TestShouldRedispatch:
    _RATE_LIMIT = Exception("429 RESOURCE_EXHAUSTED")

    def test_retryable_with_a_delivery_left(self):
        assert should_redispatch(self._RATE_LIMIT, TaskAttempt(0, 5)) is True

    def test_no_delivery_left(self):
        """The job must settle as FAILED rather than stay PROCESSING."""
        assert should_redispatch(self._RATE_LIMIT, TaskAttempt(4, 5)) is False

    def test_no_queue_context(self):
        assert should_redispatch(self._RATE_LIMIT, None) is False

    def test_finalization_phase_is_never_redispatched(self):
        """By then the report is in GCS and the side-ops may have
        partially applied -- a cost attribution row, a PDF, telemetry. A
        re-run would duplicate them and double-count spend.
        """
        assert (
            should_redispatch(
                self._RATE_LIMIT, TaskAttempt(0, 5), JobPhase.FINALIZATION
            )
            is False
        )

    def test_pipeline_phase_is_the_default(self):
        assert should_redispatch(self._RATE_LIMIT, TaskAttempt(0, 5)) is True

    def test_permanent_error_with_deliveries_left(self):
        assert (
            should_redispatch(Exception("blocked_reason SAFETY"), TaskAttempt(0, 5))
            is False
        )
