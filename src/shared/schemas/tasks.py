"""Cross-service Cloud Tasks payload schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResearchTaskPayload(BaseModel):
    """HTTP body posted by Cloud Tasks to the research worker.

    Carries job_id, company_name, cost-attribution / user context metadata,
    and W3C distributed trace propagation context.
    """

    job_id: str = Field(
        ..., min_length=1, description="Unique research job execution ID"
    )
    company_name: str = Field(
        ..., min_length=1, description="Target company to research"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="User context and cost-attribution metadata (account_id, user_id, username, business_unit, organization)",
    )
    traceparent: str | None = Field(
        default=None,
        description="W3C traceparent header value from the initiating API request",
    )
    tracestate: str | None = Field(
        default=None,
        description="W3C tracestate header value from the initiating API request",
    )
