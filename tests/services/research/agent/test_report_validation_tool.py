"""Tests for validate_final_report and aggregate_raw_search_cache."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.research.agent.sales.utils.tools import (
    aggregate_raw_search_cache,
    validate_final_report,
)
from src.utils.guardrails import GuardrailViolation, OutputValidationResult


def test_aggregate_raw_search_cache_merges_lists():
    from src.services.research.agent.sales.utils.evidence import evidence_key

    state = {
        evidence_key("FirmographicsAgent"): [{"url": "https://a.com", "snippet": "a"}],
        evidence_key("StrategyAgent"): [{"url": "https://b.com", "snippet": "b"}],
        "firmographicsagent_output": "{}",
    }
    merged = aggregate_raw_search_cache(state)
    assert len(merged) == 2


@pytest.mark.asyncio
async def test_validate_final_report_passed():
    tool_context = MagicMock()
    tool_context.state = {}

    mock_result = OutputValidationResult(is_valid=True)

    with patch(
        "src.services.research.agent.sales.utils.tools.OutputGuardrail"
    ) as mock_cls:
        mock_cls.return_value.validate = AsyncMock(return_value=mock_result)
        result = await validate_final_report("# Report\n\n## Company Snapshot\n", tool_context)

    assert result["status"] == "PASSED"
    assert tool_context.state["report_validation_status"] == "PASSED"
    assert tool_context.state["report_validation_attempts"] == 1


@pytest.mark.asyncio
async def test_validate_final_report_failed_increments_attempts():
    tool_context = MagicMock()
    tool_context.state = {"report_validation_attempts": 1}

    mock_result = OutputValidationResult(is_valid=False)
    mock_result._add("output:missing_table", "no table")

    with patch(
        "src.services.research.agent.sales.utils.tools.OutputGuardrail"
    ) as mock_cls:
        mock_cls.return_value.validate = AsyncMock(return_value=mock_result)
        result = await validate_final_report("short report", tool_context)

    assert result["status"] == "FAILED"
    assert tool_context.state["report_validation_attempts"] == 2
    assert len(result["violations"]) >= 1
