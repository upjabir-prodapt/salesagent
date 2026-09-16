"""Cloud Tasks delivery-attempt context and the re-dispatch decision.

Why this exists
---------------
The Cloud Tasks queue is already configured to retry a failed dispatch
(``scripts/create_cloud_tasks_queue.sh``: ``--max-attempts=5``,
``--min-backoff=10s``, ``--max-backoff=300s``,
``--max-retry-duration=3600s``), and it is the *only* retry layer whose
delays are on the right order of magnitude for a Vertex AI quota outage --
minutes, where the in-process agent backoff measures in seconds.

That layer was structurally dead. ``ResearchJobRunner._handle_failure``
wrote status ``FAILED`` and then re-raised, so the route returned 500,
Cloud Tasks re-dispatched, and ``ResearchTaskHandler.handle`` read the
status it had just written, found ``FAILED`` in ``TERMINAL_STATUSES``, and
returned ``{"action": "noop"}`` with HTTP 200 -- which tells Cloud Tasks
the task succeeded. Every failure therefore burned exactly one useful
attempt and turned the remaining four into no-ops.

The fix is to decide, at failure time, whether this failure is worth
re-delivering, and to leave the job non-terminal only in that case. The
decision lives here as a pure function so the runner (which writes the
status) and the handler (which chooses the HTTP response) cannot drift
apart.

What is deliberately NOT re-dispatched
--------------------------------------
* ``SAFETY`` and ``FATAL`` -- deterministic. Re-running the whole pipeline
  four more times cannot change a blocked response or a programming error.
* ``INVALID_OUTPUT`` by default -- the ReportCompiler has already spent
  its revision budget re-drafting with targeted feedback on what was
  wrong (see ``agents/compiler.py``). A cold re-run is unlikely to do
  better and costs a full pipeline. Toggle with
  ``CLOUD_TASKS_RETRY_ON_INVALID_OUTPUT``.
* Anything raised after the pipeline succeeded -- see ``JobPhase``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from src.shared.config import settings
from src.worker.agents.base import ErrorKind, classify

# Header Cloud Tasks sets on every dispatch: "The number of times this
# task has been retried. For the first attempt, this value is 0. This
# number includes attempts where the task failed due to 5XX error codes
# and never reached the execution phase." That last sentence is why this
# is the right header -- a 5XX is exactly how a retryable failure is
# signalled here. (X-CloudTasks-TaskExecutionCount excludes 5XX and so
# would stay at 0 forever.)
RETRY_COUNT_HEADER = "X-CloudTasks-TaskRetryCount"
QUEUE_NAME_HEADER = "X-CloudTasks-QueueName"

# Kinds worth re-delivering: the failure is a property of the moment, not
# of the request. RATE_LIMIT is the case this whole mechanism exists for.
QUEUE_RETRYABLE_KINDS: frozenset[ErrorKind] = frozenset(
    {
        ErrorKind.RATE_LIMIT,
        ErrorKind.TRANSIENT,
        ErrorKind.TIMEOUT,
    }
)


class JobPhase(StrEnum):
    """Which half of the job failed.

    ``FINALIZATION`` is never re-dispatched even for an otherwise
    retryable error. By that point the report has been uploaded to GCS and
    the finalization side-ops may have partially applied -- a cost
    attribution row inserted, a PDF written, telemetry flushed. Re-running
    the job would duplicate them (double-counting spend), and those ops
    already have their own in-process retry via ``services/async_retry``.
    """

    PIPELINE = "PIPELINE"
    FINALIZATION = "FINALIZATION"


@dataclass(frozen=True, slots=True)
class TaskAttempt:
    """One Cloud Tasks delivery attempt.

    ``retry_count`` is 0 on the first delivery, so the Nth delivery has
    ``retry_count == N - 1``.
    """

    retry_count: int
    max_attempts: int

    @property
    def attempt_number(self) -> int:
        """1-based delivery number, for humans."""
        return self.retry_count + 1

    @property
    def attempts_remaining(self) -> bool:
        """True if Cloud Tasks will deliver this task again after a 5XX."""
        return self.attempt_number < self.max_attempts

    @property
    def label(self) -> str:
        return f"attempt {self.attempt_number} of {self.max_attempts}"

    @classmethod
    def from_headers(cls, headers: Any) -> TaskAttempt | None:
        """Build from request headers, or None when not a queue delivery.

        Returns None for the local development path
        (``CloudTasksService._enqueue_local_http`` posts straight to the
        worker with no Cloud Tasks headers), where there is no queue to
        re-deliver anything and a failure must stay terminal.
        """
        if headers is None:
            return None
        getter = getattr(headers, "get", None)
        if not callable(getter):
            return None
        raw = getter(RETRY_COUNT_HEADER) or getter(RETRY_COUNT_HEADER.lower())
        if raw is None:
            return None
        try:
            retry_count = int(raw)
        except (TypeError, ValueError):
            return None
        return cls(
            retry_count=max(retry_count, 0),
            max_attempts=max(int(settings.CLOUD_TASKS_MAX_ATTEMPTS), 1),
        )


def is_queue_retryable(error: BaseException) -> bool:
    """True if *error* is the kind of failure a later delivery could survive."""
    kind = classify(error)
    if kind in QUEUE_RETRYABLE_KINDS:
        return True
    return (
        kind is ErrorKind.INVALID_OUTPUT
        and settings.CLOUD_TASKS_RETRY_ON_INVALID_OUTPUT
    )


def should_redispatch(
    error: BaseException,
    attempt: TaskAttempt | None,
    phase: JobPhase = JobPhase.PIPELINE,
) -> bool:
    """True if Cloud Tasks should be asked to deliver this task again.

    All three conditions must hold: the job must have failed in the
    pipeline phase (nothing irreversible applied yet), the error must be
    transient in nature, and the queue must actually have a delivery left.
    """
    if attempt is None or not attempt.attempts_remaining:
        return False
    if phase is not JobPhase.PIPELINE:
        return False
    return is_queue_retryable(error)


__all__ = [
    "JobPhase",
    "QUEUE_RETRYABLE_KINDS",
    "RETRY_COUNT_HEADER",
    "QUEUE_NAME_HEADER",
    "TaskAttempt",
    "is_queue_retryable",
    "should_redispatch",
]
