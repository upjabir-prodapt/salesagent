import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.utils.guardrails import InputGuardrail, OutputGuardrail, GuardrailViolation
from src.core.exceptions import InputValidationException

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
    # Mock all checks to return no violations
    with patch.object(guardrail, "check_narrative_bullets", return_value=[]), \
         patch.object(guardrail, "check_strategic_brief_format", return_value=[]), \
         patch.object(guardrail, "check_completeness", return_value=[]), \
         patch.object(guardrail, "check_prohibited_content", return_value=[]), \
         patch.object(guardrail, "check_hallucinations", new_callable=AsyncMock) as mock_hall:
        
        mock_hall.return_value = []
        
        result = await guardrail.validate("# Valid Report\n## Section\nSome text")
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
