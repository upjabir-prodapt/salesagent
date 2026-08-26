"""Tests for prompt module exports."""

from src.worker.agents.prompts import (
    ALIGNMENT_PROMPT,
    REPORT_COMPILER_PROMPT,
)


def test_synthesis_prompts_defined_and_non_empty() -> None:
    assert ALIGNMENT_PROMPT.strip()
    assert REPORT_COMPILER_PROMPT.strip()
    assert "{company_name?}" in ALIGNMENT_PROMPT
    assert "{{company_name?}}" in REPORT_COMPILER_PROMPT
