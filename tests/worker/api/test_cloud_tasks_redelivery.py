"""End-to-end tests for Cloud Tasks re-delivery of a failed research job.

These drive the real route -> real ResearchTaskHandler -> real
ResearchJobRunner against a *stateful* BigQuery fake, because the bug
being fixed here lived entirely in the interaction between the status the
runner wrote and the status the next delivery read back:

    _handle_failure wrote FAILED  ->  route returned 500
      ->  Cloud Tasks re-delivered
        ->  handle() read FAILED, found it in TERMINAL_STATUSES
          ->  returned {"action": "noop"} with HTTP 200
            ->  Cloud Tasks treated the task as succeeded and stopped

So every failure consumed one useful delivery and turned the queue's
remaining four (--max-attempts=5) into no-ops. The queue-level retry is
the only layer whose backoff (10s-300s, up to an hour) is on the right
order of magnitude for a Vertex AI quota outage, and it never ran.

A unit test on either side in isolation cannot catch that -- each half
was individually reasonable. Hence a loop test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status as http_status
from fastapi.testclient import TestClient

from src.worker.api.auth import require_cloud_tasks_oidc
from src.worker.api.handlers import ResearchTaskHandler
from src.worker.dependencies import get_research_task_handler
from src.worker.main import app
from src.worker.services.job_runner import ResearchJobRunner
from src.worker.services.task_attempt import RETRY_COUNT_HEADER

_JOB_ID = "job_redelivery"


class StatefulBigQuery:
    """In-memory stand-in that actually remembers what was written.

    A MagicMock cannot express this bug: the failing behaviour is that
    delivery N+1 *reads back* what delivery N wrote.
    """

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {
            _JOB_ID: {"request_id": _JOB_ID, "status": "QUEUED", "metadata": {}}
        }
        self.status_writes: list[tuple[str, str | None]] = []

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        row = self.rows.get(job_id)
        return dict(row) if row else None

    def update_status(
        self,
        job_id: str,
        status: str | None,
        gcs_uri: str | None = None,
        error: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        metadata_update: dict | None = None,
    ) -> bool:
        row = self.rows.setdefault(job_id, {"request_id": job_id})
        self.status_writes.append((job_id, status))
        # Mirrors the real repository: a None status leaves the column
        # untouched, which is what keeps a retryable job non-terminal.
        if status is not None:
            row["status"] = status
        if current_step is not None:
            row["current_step"] = current_step
        if error is not None:
            row["error_message"] = error
        if metadata_update is not None:
            row["metadata"] = {**row.get("metadata", {}), **metadata_update}
        return True

    @property
    def current_status(self) -> str:
        return self.rows[_JOB_ID]["status"]

    @property
    def metadata(self) -> dict[str, Any]:
        return self.rows[_JOB_ID].get("metadata", {})


class ScriptedPipeline:
    """Raises a scripted error per run, then succeeds."""

    def __init__(self, errors: list[Exception | None]) -> None:
        self._errors = errors
        self.runs = 0

    async def run(self, request, observer):  # noqa: ANN001 - test double
        index = min(self.runs, len(self._errors) - 1)
        self.runs += 1
        error = self._errors[index]
        if error is not None:
            raise error
        return MagicMock()


def _build_client(pipeline: ScriptedPipeline, bq: StatefulBigQuery) -> TestClient:
    finalization = MagicMock()
    finalization.finalize = AsyncMock(return_value=([], True))
    artifacts = MagicMock()
    artifacts.upload_artifacts = MagicMock(return_value="gs://bucket/report.md")
    artifacts.upload_agent_artifacts = AsyncMock()

    runner = ResearchJobRunner(
        pipeline=pipeline,
        bigquery_repository=bq,
        artifact_service=artifacts,
        finalization_service=finalization,
    )
    handler = ResearchTaskHandler(job_runner=runner, bigquery_repository=bq)

    app.dependency_overrides[require_cloud_tasks_oidc] = lambda: {
        "email": "sa@project.iam.gserviceaccount.com"
    }
    app.dependency_overrides[get_research_task_handler] = lambda: handler
    return TestClient(app, raise_server_exceptions=False)


def _deliver(client: TestClient, retry_count: int):
    """One Cloud Tasks delivery, with the header the queue really sets."""
    return client.post(
        "/internal/tasks/research",
        json={"job_id": _JOB_ID, "company_name": "Acme Corp"},
        headers={RETRY_COUNT_HEADER: str(retry_count)},
    )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def test_rate_limited_job_is_redelivered_and_then_succeeds(monkeypatch):
    """The headline case: a RESOURCE_EXHAUSTED must survive to a later
    delivery, where the quota window has had 10-300s to reset."""
    import src.worker.services.task_attempt as ta

    monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)

    bq = StatefulBigQuery()
    pipeline = ScriptedPipeline(
        [Exception("429 RESOURCE_EXHAUSTED. Quota exceeded"), None]
    )
    with _build_client(pipeline, bq) as client:
        # Delivery 1: quota exhausted.
        first = _deliver(client, retry_count=0)
        assert first.status_code == http_status.HTTP_500_INTERNAL_SERVER_ERROR
        # Crucially NOT terminal -- this is the whole fix.
        assert bq.current_status == "PROCESSING"
        assert bq.metadata["last_transient_error"].startswith("429 RESOURCE_EXHAUSTED")
        assert bq.metadata["delivery_attempts"] == 1

        # Delivery 2: the queue re-delivers and the job actually runs again.
        second = _deliver(client, retry_count=1)
        assert second.status_code == http_status.HTTP_200_OK
        assert second.json()["action"] == "ran"

    assert pipeline.runs == 2, "the second delivery must re-run the pipeline"
    assert bq.current_status == "COMPLETED"


def test_job_settles_as_failed_on_the_final_delivery(monkeypatch):
    """The job must not be left non-terminal forever. On the last delivery
    the queue will allow, the failure is recorded as FAILED."""
    import src.worker.services.task_attempt as ta

    monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 3, raising=False)

    bq = StatefulBigQuery()
    pipeline = ScriptedPipeline([Exception("429 RESOURCE_EXHAUSTED")])
    with _build_client(pipeline, bq) as client:
        assert _deliver(client, 0).status_code == 500
        assert bq.current_status == "PROCESSING"
        assert _deliver(client, 1).status_code == 500
        assert bq.current_status == "PROCESSING"

        # Third delivery is the last the queue will make (max_attempts=3),
        # so the job settles rather than staying PROCESSING forever.
        final = _deliver(client, 2)
        assert final.status_code == http_status.HTTP_200_OK
        assert final.json() == {
            "job_id": _JOB_ID,
            "status": "FAILED",
            "action": "failed",
        }

    assert pipeline.runs == 3
    assert bq.current_status == "FAILED"
    assert bq.metadata["delivery_attempts"] == 3


def test_permanent_failure_is_not_redelivered(monkeypatch):
    """A safety block is deterministic. Re-running the whole pipeline four
    more times cannot change it, so the queue is stopped on delivery 1 --
    and with a 2xx, so it is not even charged a wasted re-delivery."""
    import src.worker.services.task_attempt as ta

    monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)

    bq = StatefulBigQuery()
    pipeline = ScriptedPipeline([Exception("blocked_reason SAFETY")])
    with _build_client(pipeline, bq) as client:
        response = _deliver(client, retry_count=0)
        assert response.status_code == http_status.HTTP_200_OK
        assert response.json()["action"] == "failed"

    assert pipeline.runs == 1
    assert bq.current_status == "FAILED"


def test_invalid_output_is_not_redelivered_by_default(monkeypatch):
    """The compiler already re-drafted with targeted feedback on every
    violation; a cold full-pipeline re-run is expensive and unlikely to
    do better. Opt in with CLOUD_TASKS_RETRY_ON_INVALID_OUTPUT."""
    import src.worker.services.task_attempt as ta

    monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)
    monkeypatch.setattr(
        ta.settings, "CLOUD_TASKS_RETRY_ON_INVALID_OUTPUT", False, raising=False
    )

    bq = StatefulBigQuery()
    pipeline = ScriptedPipeline([Exception("validation failed: Section 1 missing")])
    with _build_client(pipeline, bq) as client:
        assert _deliver(client, 0).status_code == http_status.HTTP_200_OK
    assert pipeline.runs == 1
    assert bq.current_status == "FAILED"


def test_invalid_output_is_redelivered_when_enabled(monkeypatch):
    import src.worker.services.task_attempt as ta

    monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)
    monkeypatch.setattr(
        ta.settings, "CLOUD_TASKS_RETRY_ON_INVALID_OUTPUT", True, raising=False
    )

    bq = StatefulBigQuery()
    pipeline = ScriptedPipeline([Exception("validation failed: Section 1 missing")])
    with _build_client(pipeline, bq) as client:
        assert _deliver(client, 0).status_code == 500
    assert bq.current_status == "PROCESSING"


def test_direct_local_dispatch_still_fails_terminally(monkeypatch):
    """CloudTasksService._enqueue_local_http posts here with no Cloud Tasks
    headers. There is no queue to re-deliver anything, so leaving the job
    non-terminal would hang it forever in PROCESSING."""
    import src.worker.services.task_attempt as ta

    monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)

    bq = StatefulBigQuery()
    pipeline = ScriptedPipeline([Exception("429 RESOURCE_EXHAUSTED")])
    with _build_client(pipeline, bq) as client:
        response = client.post(
            "/internal/tasks/research",
            json={"job_id": _JOB_ID, "company_name": "Acme Corp"},
        )  # no RETRY_COUNT_HEADER
        assert response.status_code == http_status.HTTP_200_OK
        assert response.json()["action"] == "failed"
    assert bq.current_status == "FAILED"


def test_already_completed_job_is_still_a_noop(monkeypatch):
    """The idempotency guard must keep working: a duplicate delivery of a
    finished job must not re-run it."""
    import src.worker.services.task_attempt as ta

    monkeypatch.setattr(ta.settings, "CLOUD_TASKS_MAX_ATTEMPTS", 5, raising=False)

    bq = StatefulBigQuery()
    bq.rows[_JOB_ID]["status"] = "COMPLETED"
    pipeline = ScriptedPipeline([None])
    with _build_client(pipeline, bq) as client:
        response = _deliver(client, retry_count=1)
        assert response.status_code == http_status.HTTP_200_OK
        assert response.json()["action"] == "noop"
    assert pipeline.runs == 0
