"""Unit tests for RetryPolicy and classify() in src/worker/agents/base.py."""

from __future__ import annotations

import pytest

from src.worker.agents.base import (
    AgentError,
    ErrorKind,
    RetryPolicy,
    classify,
)


class TestClassify:
    def test_timeout_error_instance(self):
        assert classify(TimeoutError()) == ErrorKind.TIMEOUT

    def test_agent_error_passthrough(self):
        exc = AgentError("boom", kind=ErrorKind.SAFETY)
        assert classify(exc) == ErrorKind.SAFETY

    @pytest.mark.parametrize(
        "message",
        ["RESOURCE_EXHAUSTED", "429 Too Many Requests", "quota exceeded"],
    )
    def test_rate_limit_markers(self, message):
        assert classify(Exception(message)) == ErrorKind.RATE_LIMIT

    def test_timeout_markers(self):
        assert classify(Exception("request timed out")) == ErrorKind.TIMEOUT

    @pytest.mark.parametrize(
        "message", ["content blocked_reason SAFETY", "HARM_CATEGORY_HARASSMENT"]
    )
    def test_safety_markers(self, message):
        assert classify(Exception(message)) == ErrorKind.SAFETY

    def test_invalid_output_markers(self):
        assert (
            classify(Exception("missing_output for agent")) == ErrorKind.INVALID_OUTPUT
        )

    def test_connect_markers_are_transient(self):
        assert classify(Exception("Connection reset by peer")) == ErrorKind.TRANSIENT

    def test_unrecognized_is_fatal(self):
        assert classify(Exception("some completely unrelated error")) == ErrorKind.FATAL

    def test_status_code_attribute_429(self):
        exc = Exception("oops")
        exc.status_code = 429  # type: ignore[attr-defined]
        assert classify(exc) == ErrorKind.RATE_LIMIT

    def test_status_code_attribute_503(self):
        exc = Exception("oops")
        exc.status_code = 503  # type: ignore[attr-defined]
        assert classify(exc) == ErrorKind.TRANSIENT


class TestRetryPolicy:
    def test_should_retry_true_within_budget(self):
        policy = RetryPolicy(max_attempts=3)
        assert policy.should_retry(ErrorKind.TIMEOUT, 1) is True
        assert policy.should_retry(ErrorKind.TIMEOUT, 2) is True

    def test_should_retry_false_at_budget(self):
        policy = RetryPolicy(max_attempts=3)
        assert policy.should_retry(ErrorKind.TIMEOUT, 3) is False

    def test_should_retry_false_for_non_retryable_kind(self):
        policy = RetryPolicy(max_attempts=5)
        assert policy.should_retry(ErrorKind.FATAL, 1) is False
        assert policy.should_retry(ErrorKind.SAFETY, 1) is False

    def test_custom_retry_on_set(self):
        policy = RetryPolicy(max_attempts=5, retry_on=frozenset({ErrorKind.SAFETY}))
        assert policy.should_retry(ErrorKind.SAFETY, 1) is True
        assert policy.should_retry(ErrorKind.TIMEOUT, 1) is False

    def test_delay_for_grows_exponentially_without_jitter(self):
        policy = RetryPolicy(initial_delay=1.0, exp_base=2.0, jitter=0.0, max_delay=100)
        assert policy.delay_for(1) == 1.0
        assert policy.delay_for(2) == 2.0
        assert policy.delay_for(3) == 4.0

    def test_delay_for_caps_at_max_delay(self):
        policy = RetryPolicy(
            initial_delay=10.0, exp_base=2.0, jitter=0.0, max_delay=15.0
        )
        assert policy.delay_for(5) == 15.0

    def test_delay_for_jitter_stays_non_negative_and_bounded(self):
        policy = RetryPolicy(initial_delay=1.0, exp_base=2.0, jitter=0.5, max_delay=100)
        for attempt in range(1, 6):
            raw = min(1.0 * (2.0 ** (attempt - 1)), 100)
            for _ in range(20):
                delay = policy.delay_for(attempt)
                assert delay >= 0.0
                assert delay <= raw * 1.5 + 1e-9

    def test_default_policy_is_reasonable(self):
        policy = RetryPolicy()
        assert policy.max_attempts == 3
        assert policy.timeout == 120.0
        assert ErrorKind.FATAL not in policy.retry_on
        assert ErrorKind.SAFETY not in policy.retry_on


class TestAgentError:
    def test_agent_error_carries_metadata(self):
        cause = ValueError("root cause")
        exc = AgentError(
            "wrapped",
            kind=ErrorKind.TRANSIENT,
            agent_name="QueryPlanner",
            attempts=2,
            cause=cause,
        )
        assert exc.kind == ErrorKind.TRANSIENT
        assert exc.agent_name == "QueryPlanner"
        assert exc.attempts == 2
        assert exc.cause is cause
        assert str(exc) == "wrapped"
