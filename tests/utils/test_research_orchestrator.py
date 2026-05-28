from __future__ import annotations

import pytest

from src.services.research.application.orchestrator import ResearchJobOrchestrator


class _StatusRepoStub:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def update_status(self, job_id: str, status: str | None, **kwargs):
        self.calls.append({"job_id": job_id, "status": status, **kwargs})
        return True


class _RunnerStub:
    def __init__(self, final_report: str = "# report", state: dict | None = None) -> None:
        self.final_report = final_report
        self.state = state or {"report_validation_status": "PASSED"}

    async def run(self, job_id: str, company_name: str):
        return self.final_report, dict(self.state)


class _ArtifactStub:
    def __init__(self) -> None:
        self.upload_called = False
        self.upload_agents_called = False

    def upload_artifacts(self, job_id: str, final_report: str, session_state: dict) -> str:
        self.upload_called = True
        return f"gs://bucket/{job_id}/final_report.md"

    async def upload_agent_artifacts(self, job_id: str, session_state: dict):
        self.upload_agents_called = True
        return {}


class _FinalizationStub:
    def __init__(self, *, fail_failure_export: bool = False) -> None:
        self.failure_exports: list[dict] = []
        self.fail_failure_export = fail_failure_export

    async def finalize(self, job_id: str, final_report: str, session_state: dict, metrics: dict):
        return {}, True

    async def export_failure_telemetry(
        self, job_id: str, session_state: dict, metrics: dict
    ) -> dict[str, str]:
        if self.fail_failure_export:
            raise RuntimeError("telemetry export failed")
        self.failure_exports.append(
            {"job_id": job_id, "session_state": session_state, "metrics": metrics}
        )
        return {}


@pytest.mark.asyncio
async def test_orchestrator_marks_completed_on_success() -> None:
    status = _StatusRepoStub()
    artifacts = _ArtifactStub()
    orchestrator = ResearchJobOrchestrator(
        status_repository=status,
        runner=_RunnerStub(state={"report_validation_status": "PASSED"}),
        artifacts=artifacts,
        finalization=_FinalizationStub(),
    )

    await orchestrator.run("job-1", "Acme Corp")

    assert status.calls[0]["status"] == "PROCESSING"
    assert status.calls[-1]["status"] == "COMPLETED"
    assert artifacts.upload_called is True
    assert artifacts.upload_agents_called is True


@pytest.mark.asyncio
async def test_orchestrator_marks_failed_when_validation_fails() -> None:
    status = _StatusRepoStub()
    artifacts = _ArtifactStub()
    finalization = _FinalizationStub()
    runner = _RunnerStub(
        state={
            "report_validation_status": "FAILED",
            "report_validation_violations": [{"rule": "missing_source", "detail": "No source"}],
            "agent_telemetry_records": [{"record_id": "r1", "agent_name": "ReportCompiler"}],
        }
    )
    orchestrator = ResearchJobOrchestrator(
        status_repository=status,
        runner=runner,
        artifacts=artifacts,
        finalization=finalization,
    )

    await orchestrator.run("job-2", "Globex")

    assert status.calls[-1]["status"] == "FAILED"
    assert str(status.calls[-1]["error"]).startswith("Output blocked:")
    assert status.calls[-1]["metadata_update"]["report_validation_status"] == "FAILED"
    assert status.calls[-1]["metadata_update"]["latency_seconds"] is not None
    assert artifacts.upload_called is False
    assert len(finalization.failure_exports) == 1
    assert finalization.failure_exports[0]["job_id"] == "job-2"


@pytest.mark.asyncio
async def test_orchestrator_still_marks_failed_when_failure_telemetry_export_fails() -> None:
    status = _StatusRepoStub()
    artifacts = _ArtifactStub()
    finalization = _FinalizationStub(fail_failure_export=True)
    runner = _RunnerStub(
        state={
            "report_validation_status": "FAILED",
            "report_validation_violations": [{"rule": "missing_source", "detail": "No source"}],
        }
    )
    orchestrator = ResearchJobOrchestrator(
        status_repository=status,
        runner=runner,
        artifacts=artifacts,
        finalization=finalization,
    )

    await orchestrator.run("job-telemetry-fail", "Globex")

    assert status.calls[-1]["status"] == "FAILED"
    assert (
        status.calls[-1]["metadata_update"]["side_op_failures"]["failure_telemetry_export"]
        == "telemetry export failed"
    )
    assert artifacts.upload_called is False


@pytest.mark.asyncio
async def test_orchestrator_marks_failed_when_runner_raises() -> None:
    class _FailingRunner:
        async def run(self, job_id: str, company_name: str):
            raise RuntimeError("runner exploded")

    status = _StatusRepoStub()
    orchestrator = ResearchJobOrchestrator(
        status_repository=status,
        runner=_FailingRunner(),
        artifacts=_ArtifactStub(),
        finalization=_FinalizationStub(),
    )

    with pytest.raises(RuntimeError):
        await orchestrator.run("job-3", "Initech")

    assert status.calls[-1]["status"] == "FAILED"
    assert "runner exploded" in status.calls[-1]["metadata_update"]["raw_error"]
