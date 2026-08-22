from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from src.services.research.agents.sales.callbacks.plan_react import (
    REPORT_COMPILER_PHASE_ERROR_KEY,
    REPORT_VALIDATION_TOOL_CALL_COUNT_KEY,
    plan_after_model,
    plan_after_tool,
    plan_before_model,
)
from src.services.research.agents.sales.tools import (
    VALIDATE_FINAL_REPORT_TOOL,
)


@dataclass
class _Part:
    text: str
    thought: bool = False


@dataclass
class _Content:
    role: str
    parts: list[_Part]


class _LlmRequestStub:
    def __init__(self, user_text: str = "normal user request") -> None:
        self.contents = [_Content(role="user", parts=[_Part(user_text)])]
        self.instructions: list[str] = []

    def append_instructions(self, values: list[str]) -> None:
        self.instructions.extend(values)


class _CallbackContextStub:
    def __init__(self, agent_name: str, state: dict | None = None) -> None:
        self.agent_name = agent_name
        self.state = {} if state is None else state


class _ToolContextStub:
    def __init__(self, agent_name: str, state: dict | None = None) -> None:
        self.agent_name = agent_name
        self.state = {} if state is None else state


class _ToolStub:
    def __init__(self, name: str) -> None:
        self.name = name


def _response_with_text(text: str):
    return SimpleNamespace(content=SimpleNamespace(parts=[_Part(text)]))








def test_report_compiler_before_model_injects_strict_planreact_instruction() -> None:
    callback_context = _CallbackContextStub(agent_name="ReportCompiler", state={})
    llm_request = _LlmRequestStub()

    result = plan_before_model(callback_context, llm_request)

    assert result is None
    combined = " ".join(llm_request.instructions)
    assert "/*PLANNING*/" in combined
    assert "/*AGGREGATED_ANSWER*/" in combined
    assert "validate_final_report" in combined


def test_report_compiler_validate_tool_call_is_counted() -> None:
    state: dict = {
        "report_validation_status": "FAILED",
        "report_validation_violations": [],
    }
    tool_context = _ToolContextStub(agent_name="ReportCompiler", state=state)

    result = plan_after_tool(
        _ToolStub(VALIDATE_FINAL_REPORT_TOOL),
        args={"draft": "## draft"},
        tool_context=tool_context,
        tool_response={"status": "FAILED", "violations": []},
    )

    assert result is None
    assert state.get(REPORT_VALIDATION_TOOL_CALL_COUNT_KEY) == 1


def test_report_compiler_direct_markdown_without_planreact_tags_fails_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.services.research.agents.sales.callbacks.plan_react.EvidenceStore.ingest_grounding",
        lambda *args, **kwargs: None,
    )
    state: dict = {}
    callback_context = _CallbackContextStub(agent_name="ReportCompiler", state=state)

    plan_after_model(
        callback_context,
        _response_with_text("## Company Snapshot\n- Company Name: Example Corp"),
    )

    assert state.get("report_validation_status") == "FAILED"
    assert "PlanReAct tags" in str(state.get(REPORT_COMPILER_PHASE_ERROR_KEY))


def test_report_compiler_final_answer_after_passed_validation_ignores_missing_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.services.research.agents.sales.callbacks.plan_react.EvidenceStore.ingest_grounding",
        lambda *args, **kwargs: None,
    )
    state: dict = {
        "report_validation_status": "PASSED",
        "report_validation_violations": [],
    }
    callback_context = _CallbackContextStub(agent_name="ReportCompiler", state=state)

    plan_after_model(
        callback_context,
        _response_with_text(
            "/*FINAL_ANSWER*/\n## Company Snapshot\n- Company Name: Example"
        ),
    )

    assert state.get("report_validation_status") == "PASSED"
    assert REPORT_COMPILER_PHASE_ERROR_KEY not in state


def test_report_compiler_final_answer_without_passed_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "src.services.research.agents.sales.callbacks.plan_react.EvidenceStore.ingest_grounding",
        lambda *args, **kwargs: None,
    )
    state: dict = {
        "report_compiler_seen_planreact_phases": [
            "/*PLANNING*/",
            "/*AGGREGATED_ANSWER*/",
        ],
    }
    callback_context = _CallbackContextStub(agent_name="ReportCompiler", state=state)

    plan_after_model(
        callback_context,
        _response_with_text(
            "/*FINAL_ANSWER*/\n## Company Snapshot\n- Company Name: Example"
        ),
    )

    assert state.get("report_validation_status") == "FAILED"
    violations = state.get("report_validation_violations") or []
    assert violations and violations[0]["rule"] == "output:validation_not_passed"



