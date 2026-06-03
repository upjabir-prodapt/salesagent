import pytest

from src.core.exceptions import InputValidationException
from src.utils.guardrails import InputGuardrail, OutputGuardrail


def _valid_report(
    include_section_8_table: bool = True, section_8_extra: str = ""
) -> str:
    section_8_table = (
        "| Business / IT Challenge or Priority | Colt Solution Enabler(s) | Alignment Justification |\n"
        "| --- | --- | --- |\n"
        "| Legacy network performance | Colt DIA | Improves performance |\n"
        if include_section_8_table
        else ""
    )
    return f"""
## Company Snapshot
Snapshot data.

## 1. Company Overview
Overview content.

## 2. Key Executive Bios
Executive content.

## 3. Strategic Priorities and Business Goals (Next 2-5 Years)
Priorities content.

## 4. Current Market Position & Outlook
Market content.

## 5. Technology Landscape
Technology content.

## 6. Key Business & IT Challenges
Challenges content.

## 7. Procurement & Technology Buying Patterns
Procurement content.

## 8. Colt Technology Alignment Table
{section_8_table}
{section_8_extra}

## 9. Relationship Landscape & Potential Synergies
Narrative content.

## 10. Regional Spend & Infrastructure Overlay
No table required by active validation scope.

## 11. Strategic Opportunity & Live Call Readiness
Opportunity content.

## 12. Signals
Signals prose.

## 13. Source Summary
https://example.com/source
"""


def test_input_guardrail_valid():
    guardrail = InputGuardrail()
    # Should not raise
    guardrail.validate("Valid Company Name")


def test_input_guardrail_pii_detected():
    guardrail = InputGuardrail()
    # Test with an email pattern
    with pytest.raises(InputValidationException):
        guardrail.validate("My email is test@example.com")


def test_input_guardrail_jailbreak_detected():
    guardrail = InputGuardrail()
    with pytest.raises(InputValidationException):
        guardrail.validate("This is a jailbreak attempt")


@pytest.mark.asyncio
async def test_output_guardrail_valid():
    guardrail = OutputGuardrail()
    result = await guardrail.validate(_valid_report())
    assert result.is_valid is True


def test_check_narrative_bullets():
    guardrail = OutputGuardrail()
    report = "## 12. Signals\n* Bullet point"
    violations = guardrail.check_narrative_bullets(report)
    assert len(violations) > 0
    assert violations[0].rule == "output:narrative_bullets"


def test_check_strategic_brief_format():
    guardrail = OutputGuardrail()
    report = "Missing headers"
    violations = guardrail.check_strategic_brief_format(report)
    assert len(violations) > 0
    assert any(v.rule == "output:missing_section" for v in violations)


def test_check_strategic_brief_format_missing_section_8_table():
    guardrail = OutputGuardrail()
    report = _valid_report(
        include_section_8_table=False,
        section_8_extra="Alignment content without table rows.",
    )
    violations = guardrail.check_strategic_brief_format(report)
    assert any(v.rule == "output:missing_table" for v in violations)


@pytest.mark.asyncio
async def test_output_guardrail_validate_ignores_non_scope_checks():
    guardrail = OutputGuardrail()
    report = _valid_report(
        section_8_extra="This draft includes a buy recommendation phrase intentionally."
    )
    result = await guardrail.validate(report)
    assert result.is_valid is True


def test_check_completeness():
    guardrail = OutputGuardrail()
    report = "## 1. Section\nData"
    violations = guardrail.check_completeness(report)
    assert len(violations) > 0


def test_check_prohibited_content():
    guardrail = OutputGuardrail()
    report = "I recommend to buy this stock"
    violations = guardrail.check_prohibited_content(report)
    assert len(violations) > 0
