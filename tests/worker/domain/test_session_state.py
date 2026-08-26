from __future__ import annotations

from src.worker.domain.session_state import ResearchSessionState


def test_session_state_getters_setters_and_tracked_outputs() -> None:
    state: dict = {
        "final_report": "  report body  ",
        "job_evidence": [{"url": "https://reuters.com/x"}],
        "agent_telemetry_records": [{"agent_name": "QueryGeneratorAgent"}],
        "report_validation_status": "PASSED",
        "query_generator_output": "query text",
        "alignment_output": "",
    }
    session = ResearchSessionState(state)

    assert session.final_report == "  report body  "
    session.final_report = "updated"
    assert state["final_report"] == "updated"

    assert len(session.job_evidence) == 1
    session.job_evidence = []
    assert state["job_evidence"] == []

    assert len(session.telemetry_records) == 1
    session.telemetry_records = [{"agent_name": "AlignmentAnalyst"}]
    assert state["agent_telemetry_records"][0]["agent_name"] == "AlignmentAnalyst"

    assert session.verification_status == "PASSED"
    session.verification_status = "FAILED"
    assert state["report_validation_status"] == "FAILED"

    assert session.get_agent_output("QueryGeneratorAgent") == "query text"
    assert session.get_agent_output("UnknownAgent") == ""
    session.set_agent_output("AlignmentAnalyst", "alignment text")
    assert state["alignment_output"] == "alignment text"

    tracked = session.tracked_outputs()
    assert tracked["QueryGeneratorAgent"] == "query text"
    # AlignmentAnalyst is a tracked contract agent, so the value just written
    # above surfaces here; only agents with an empty output are omitted.
    assert tracked["AlignmentAnalyst"] == "alignment text"
    assert "ResearchSynthesizer" not in tracked


def test_session_state_defaults_for_non_list_fields() -> None:
    session = ResearchSessionState(
        {
            "job_evidence": "not-a-list",
            "agent_telemetry_records": None,
            "report_validation_status": None,
        }
    )

    assert session.job_evidence == []
    assert session.telemetry_records == []
    assert session.verification_status is None
    assert session.get_agent_output("ReportCompiler") == ""
