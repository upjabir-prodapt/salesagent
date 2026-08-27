"""Tests for ResearchJobRunner (src/worker/services/job_runner.py).

Covers the success path (PROCESSING -> pipeline -> artifacts ->
finalization -> COMPLETED) and the failure path (any exception ->
FAILED status + re-raise), including the ExceptionGroup normalization
for collapsed parallel execution.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.worker.agents.models import (
    ColtAlignment,
    PipelineResult,
    Report,
    SearchFindings,
)
from src.worker.services.job_runner import ResearchJobRunner


def _make_pipeline_result(*, validation_status: str = "PASSED") -> PipelineResult:
    return PipelineResult(
        report=Report(markdown="# Report", validation_status=validation_status),
        findings=SearchFindings(company="Acme", domains={}, executed=5, failed=()),
        alignment=ColtAlignment(mappings=(), opportunity_summary="Why Colt"),
        telemetry_records=[{"agent_name": "QueryPlanner"}],
        token_usage_by_model={"gemini-3.5-flash": {"input": 100, "output": 50}},
    )


@pytest.fixture
def bq_repo():
    repo = MagicMock()
    repo.update_status = MagicMock(return_value=True)
    return repo


@pytest.fixture
def artifact_service():
    svc = MagicMock()
    svc.upload_artifacts = MagicMock(return_value="gs://bucket/job/final_report.md")
    svc.upload_agent_artifacts = AsyncMock(return_value={})
    return svc


@pytest.fixture
def finalization_service():
    svc = MagicMock()
    svc.finalize = AsyncMock(return_value=({}, True))
    return svc


@pytest.fixture
def pipeline():
    p = MagicMock()
    p.run = AsyncMock(return_value=_make_pipeline_result())
    return p


@pytest.mark.asyncio
async def test_run_success_marks_processing_then_completed(
    pipeline, bq_repo, artifact_service, finalization_service
):
    runner = ResearchJobRunner(
        pipeline, bq_repo, artifact_service, finalization_service
    )

    await runner.run("job_1", "Acme Corp", metadata={"user_id": "u@colt.net"})

    statuses = [call.args[1] for call in bq_repo.update_status.call_args_list]
    assert "PROCESSING" in statuses
    assert statuses[-1] == "COMPLETED"

    completed_call = bq_repo.update_status.call_args_list[-1]
    assert completed_call.kwargs["gcs_uri"] == "gs://bucket/job/final_report.md"
    assert completed_call.kwargs["progress"] == 100

    artifact_service.upload_artifacts.assert_called_once()
    artifact_service.upload_agent_artifacts.assert_called_once()
    finalization_service.finalize.assert_called_once()


@pytest.mark.asyncio
async def test_run_success_passes_session_state_from_legacy_bridge(
    pipeline, bq_repo, artifact_service, finalization_service
):
    runner = ResearchJobRunner(
        pipeline, bq_repo, artifact_service, finalization_service
    )

    await runner.run("job_1", "Acme Corp")

    upload_call = artifact_service.upload_artifacts.call_args
    session_state = upload_call.args[2]
    assert session_state["company_name"] == "Acme"
    assert session_state["report_validation_status"] == "PASSED"
    assert session_state["mc_search_count"] == 5


@pytest.mark.asyncio
async def test_run_artifact_upload_failure_is_non_fatal(
    pipeline, bq_repo, artifact_service, finalization_service
):
    artifact_service.upload_agent_artifacts = AsyncMock(
        side_effect=RuntimeError("gcs down")
    )
    runner = ResearchJobRunner(
        pipeline, bq_repo, artifact_service, finalization_service
    )

    # Should not raise -- per-agent artifact upload failures are logged only.
    await runner.run("job_1", "Acme Corp")

    statuses = [call.args[1] for call in bq_repo.update_status.call_args_list]
    assert statuses[-1] == "COMPLETED"


@pytest.mark.asyncio
async def test_run_pipeline_failure_marks_job_failed_and_reraises(
    bq_repo, artifact_service, finalization_service
):
    pipeline = MagicMock()
    pipeline.run = AsyncMock(side_effect=RuntimeError("model exploded"))
    runner = ResearchJobRunner(
        pipeline, bq_repo, artifact_service, finalization_service
    )

    with pytest.raises(RuntimeError, match="model exploded"):
        await runner.run("job_2", "Acme Corp")

    failed_call = bq_repo.update_status.call_args_list[-1]
    assert failed_call.args[1] == "FAILED"
    assert failed_call.kwargs["error"] == "model exploded"
    artifact_service.upload_artifacts.assert_not_called()
    finalization_service.finalize.assert_not_called()


@pytest.mark.asyncio
async def test_run_exception_group_normalizes_error_message(
    bq_repo, artifact_service, finalization_service
):
    pipeline = MagicMock()
    pipeline.run = AsyncMock(
        side_effect=ExceptionGroup("multi", [RuntimeError("429"), RuntimeError("429")])
    )
    runner = ResearchJobRunner(
        pipeline, bq_repo, artifact_service, finalization_service
    )

    with pytest.raises(ExceptionGroup):
        await runner.run("job_3", "Acme Corp")

    failed_call = bq_repo.update_status.call_args_list[-1]
    assert failed_call.args[1] == "FAILED"
    assert "Quota/QPM" in failed_call.kwargs["error"]


@pytest.mark.asyncio
async def test_run_records_span_attributes_on_success(
    pipeline, bq_repo, artifact_service, finalization_service
):
    span = MagicMock()
    runner = ResearchJobRunner(
        pipeline, bq_repo, artifact_service, finalization_service
    )

    await runner.run("job_1", "Acme Corp", span=span)

    span.set_attribute.assert_any_call("research.status", "completed")


@pytest.mark.asyncio
async def test_run_records_span_exception_on_failure(
    bq_repo, artifact_service, finalization_service
):
    pipeline = MagicMock()
    pipeline.run = AsyncMock(side_effect=RuntimeError("boom"))
    span = MagicMock()
    runner = ResearchJobRunner(
        pipeline, bq_repo, artifact_service, finalization_service
    )

    with pytest.raises(RuntimeError):
        await runner.run("job_1", "Acme Corp", span=span)

    span.record_exception.assert_called_once()
    span.set_attribute.assert_any_call("research.status", "failed")
