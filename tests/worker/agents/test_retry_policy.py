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


class TestRateLimitBudget:
    """RATE_LIMIT draws on its own, larger budget.

    A Vertex quota window is enforced per minute, so retrying a 429 on
    the same ~1s/2s schedule used for a malformed model response just
    re-hammers the wall. These fields default to None ("no different from
    any other retryable error") so a policy built directly behaves as it
    always did; production opts in via build_research_pipeline().
    """

    def test_defaults_leave_rate_limit_undifferentiated(self):
        policy = RetryPolicy(max_attempts=3)
        assert policy.rate_limit_max_attempts is None
        assert policy.max_attempts_for(ErrorKind.RATE_LIMIT) == 3
        assert policy.attempt_ceiling == 3
        assert policy.should_retry(ErrorKind.RATE_LIMIT, 3) is False

    def test_rate_limit_gets_more_attempts_than_other_kinds(self):
        policy = RetryPolicy(max_attempts=3, rate_limit_max_attempts=6)
        assert policy.max_attempts_for(ErrorKind.RATE_LIMIT) == 6
        assert policy.max_attempts_for(ErrorKind.TRANSIENT) == 3
        assert policy.should_retry(ErrorKind.RATE_LIMIT, 5) is True
        assert policy.should_retry(ErrorKind.TRANSIENT, 5) is False
        assert policy.should_retry(ErrorKind.RATE_LIMIT, 6) is False

    def test_attempt_ceiling_sums_the_per_kind_budgets(self):
        """tenacity's stop runs before the error is classified, so it can
        only bound the loop globally; the per-kind budget is enforced by
        the retry predicate. The bound must therefore be the SUM -- a run
        can legitimately spend its whole rate-limit budget on 429s AND
        its whole ordinary budget on validation failures."""
        assert (
            RetryPolicy(max_attempts=3, rate_limit_max_attempts=6).attempt_ceiling == 9
        )
        assert (
            RetryPolicy(max_attempts=7, rate_limit_max_attempts=2).attempt_ceiling == 9
        )
        assert RetryPolicy(max_attempts=3).attempt_ceiling == 3

    def test_rate_limit_backoff_is_longer(self):
        policy = RetryPolicy(
            initial_delay=1.0,
            max_delay=30.0,
            rate_limit_initial_delay=15.0,
            rate_limit_max_delay=120.0,
            jitter=0.0,
        )
        assert policy.delay_for(1) == 1.0
        assert policy.delay_for(1, ErrorKind.RATE_LIMIT) == 15.0
        assert policy.delay_for(3, ErrorKind.RATE_LIMIT) == 60.0
        assert policy.delay_for(4, ErrorKind.RATE_LIMIT) == 120.0
        # Capped by the rate-limit ceiling, not the ordinary one.
        assert policy.delay_for(9, ErrorKind.RATE_LIMIT) == 120.0
        assert policy.delay_for(9) == 30.0

    def test_production_rate_limit_budget_outlasts_a_quota_window(self):
        """The whole point of the change: total backoff must exceed the
        60s per-minute Vertex quota window by a wide margin. The stack
        this replaced waited ~47-55s across every layer combined."""
        policy = RetryPolicy(
            max_attempts=3,
            rate_limit_max_attempts=6,
            rate_limit_initial_delay=15.0,
            rate_limit_max_delay=120.0,
            jitter=0.0,
        )
        sleeps = [
            policy.delay_for(n, ErrorKind.RATE_LIMIT)
            for n in range(1, policy.max_attempts_for(ErrorKind.RATE_LIMIT))
        ]
        assert sleeps == [15.0, 30.0, 60.0, 120.0, 120.0]
        assert sum(sleeps) == 345.0
        assert sum(sleeps) > 60.0 * 5

    def test_non_retryable_kinds_stay_non_retryable(self):
        policy = RetryPolicy(max_attempts=3, rate_limit_max_attempts=6)
        assert policy.should_retry(ErrorKind.SAFETY, 1) is False
        assert policy.should_retry(ErrorKind.FATAL, 1) is False


class TestStepBudget:
    def test_max_elapsed_defaults_to_unbounded(self):
        assert RetryPolicy().max_elapsed is None

    def test_max_elapsed_is_carried_on_the_policy(self):
        assert RetryPolicy(max_elapsed=540.0).max_elapsed == 540.0

    def test_step_budgets_fit_the_cloud_tasks_dispatch_deadline(self):
        """The four per-step ceilings must provably fit inside the 1800s
        Cloud Tasks dispatch deadline, with room for finalization. The
        configuration this replaced had a worst case of 2413s."""
        from src.shared.config import settings

        total = (
            settings.PLANNER_STEP_BUDGET_SECONDS
            + settings.SEARCH_STEP_BUDGET_SECONDS
            + settings.ALIGNMENT_STEP_BUDGET_SECONDS
            + settings.COMPILER_STEP_BUDGET_SECONDS
        )
        assert total == 1560.0
        assert total < settings.CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS
        # At least 200s left for PDF render, GCS upload and evaluation.
        assert settings.CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS - total >= 200


class TestBuildRetrying:
    def test_build_retrying_returns_a_fresh_loop_each_call(self):
        """tenacity keeps its RetryCallState on the instance, so a shared
        loop would interleave attempt numbers across concurrent jobs."""
        policy = RetryPolicy(max_attempts=3)
        assert policy.build_retrying() is not policy.build_retrying()
