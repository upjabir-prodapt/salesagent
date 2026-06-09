from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.research_schemas import ResearchInitiateRequest


def test_research_initiate_request_accepts_valid_payload() -> None:
    req = ResearchInitiateRequest(
        account_id="0011234567890123",
        company_name="Acme Corp",
    )
    assert req.account_id == "0011234567890123"
    assert req.company_name == "Acme Corp"


def test_research_initiate_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResearchInitiateRequest(
            account_id="ACC123",
            company_name="Acme Corp",
            user_id="0051234567890123",
        )


def test_research_initiate_request_rejects_invalid_company_name() -> None:
    with pytest.raises(ValidationError):
        ResearchInitiateRequest(
            account_id="ACC123",
            company_name="Acme<script>",
        )


def test_research_initiate_request_rejects_invalid_account_id() -> None:
    with pytest.raises(ValidationError):
        ResearchInitiateRequest(
            account_id="ACC 123",
            company_name="Acme Corp",
        )
