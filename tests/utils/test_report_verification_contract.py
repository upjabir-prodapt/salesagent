from __future__ import annotations

import pytest

from src.core.config import settings
from src.services.research.agents.sales.tools.report_validation import (
    validate_final_report,
)


class _ToolContextStub:
    def __init__(self, state: dict) -> None:
        self.state = state


class _Violation:
    def __init__(self, rule: str, detail: str) -> None:
        self.rule = rule
        self.detail = detail


class _GuardrailResult:
    def __init__(self, is_valid: bool, violations: list[_Violation]) -> None:
        self.is_valid = is_valid
        self.violations = violations


@pytest.mark.asyncio
async def test_report_verification_exhausted_attempts_is_nonfatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_validate(self, draft: str, raw_search_cache=None):
        return _GuardrailResult(
            is_valid=False,
            violations=[_Violation("missing_sources", "No source summary URLs found.")],
        )

    monkeypatch.setattr(
        "src.services.research.agents.sales.tools.report_validation.OutputGuardrail.validate",
        _fake_validate,
    )
    monkeypatch.setattr(settings, "OUTPUT_GUARDRAIL_MAX_RETRIES", 1)

    state = {"report_validation_attempts": 1}
    response = await validate_final_report("## draft", _ToolContextStub(state))

    assert response["status"] == "FAILED"
    assert response["attempt"] == 2
    assert response["max_attempts"] == 2
    assert "Maximum validation attempts reached" in response["message"]
    assert state["report_validation_status"] == "FAILED"
    assert state["report_validation_terminal"] is True


@pytest.mark.asyncio
async def test_report_verification_returns_failed_status_before_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_validate(self, draft: str, raw_search_cache=None):
        return _GuardrailResult(
            is_valid=False,
            violations=[_Violation("missing_sources", "No source summary URLs found.")],
        )

    monkeypatch.setattr(
        "src.services.research.agents.sales.tools.report_validation.OutputGuardrail.validate",
        _fake_validate,
    )
    monkeypatch.setattr(settings, "OUTPUT_GUARDRAIL_MAX_RETRIES", 2)

    state = {"report_validation_attempts": 1}
    response = await validate_final_report("## draft", _ToolContextStub(state))

    assert response["status"] == "FAILED"
    assert response["attempt"] == 2
    assert response["max_attempts"] == 3
    assert "fix the draft per violations" in response["message"]
