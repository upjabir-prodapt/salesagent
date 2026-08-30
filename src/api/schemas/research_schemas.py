"""Research Schemas - Pydantic Models for Research Domain"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ResearchInitiateRequest(BaseModel):
    """Request model for initiating research"""

    account_id: str = Field(
        ...,
        description="Account identifier",
        min_length=2,
        max_length=50,
        pattern=r"^[a-zA-Z0-9\-_]+$",
    )
    company_name: str = Field(
        ...,
        description="Company name to research",
        min_length=2,
        max_length=100,
        pattern=r"^[a-zA-Z0-9\s\&\.\-\',]+$",
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"account_id": "ACC-123", "company_name": "Microsoft"}
        },
    )


class ResearchInitiateResponse(BaseModel):
    """Response model for research initiation (202 Accepted)"""

    job_id: str = Field(..., description="Unique job ID for tracking")
    status: str = Field(..., description="Initial status (PENDING)")
    check_status_url: str = Field(..., description="URL to poll job status")

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "status": "PENDING",
                "check_status_url": "/api/v1/research/status/job_123e4567-e89b-12d3-a456-426614174000",
            }
        }
    }


class ResearchStatusResponse(BaseModel):
    """Response model for status polling"""

    request_id: str = Field(..., description="Job ID")
    job_id: str | None = Field(None, description="Alias for request_id")
    status: str = Field(
        ...,
        description="Current status: QUEUED, PROCESSING, COMPLETED, FAILED, CANCELLED",
    )
    progress: int = Field(..., description="Progress percentage (0-100)")
    current_step: str | None = Field(
        None, description="Human-readable current processing step"
    )
    current_agent: str | None = Field(
        None, description="Name of the agent currently executing (PROCESSING only)"
    )

    def model_post_init(self, __context: Any) -> None:
        if self.job_id is None:
            self.job_id = self.request_id

    model_config = {
        "json_schema_extra": {
            "example": {
                "request_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "job_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "status": "PROCESSING",
                "progress": 45,
                "current_step": "Running: QueryPlanner",
                "current_agent": "QueryPlanner",
            }
        }
    }


class ModelCard(BaseModel):
    """Cost and model metadata for the completed research run"""

    model_version: str | None = Field(None, description="Model used for research")
    tokens_used: int | None = Field(None, description="Total tokens consumed")
    latency_seconds: float | None = Field(
        None, description="End-to-end agent latency in seconds"
    )
    cost_usd: float | None = Field(None, description="Total session cost in USD")

    model_config = {
        "json_schema_extra": {
            "example": {
                "model_version": "gemini-2.5-pro",
                "tokens_used": 28500,
                "latency_seconds": 185.0,
                "cost_usd": 0.35,
            }
        }
    }


class ResearchResultResponse(BaseModel):
    """Response model for completed research result"""

    request_id: str = Field(..., description="Job ID")
    job_id: str | None = Field(None, description="Alias for request_id")
    status: str = Field(..., description="Final status")
    report_content: str | None = Field(None, description="Full markdown report content")
    report_markdown: str | None = Field(
        None, description="Alias for report_content for backward compatibility"
    )
    download_url: str | None = Field(
        None, description="Signed GCS URL for downloading the report"
    )
    model_card: ModelCard | None = Field(None, description="Model and cost metadata")

    def model_post_init(self, __context: Any) -> None:
        if self.job_id is None:
            self.job_id = self.request_id
        if self.report_markdown is None:
            self.report_markdown = self.report_content

    model_config = {
        "json_schema_extra": {
            "example": {
                "request_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "job_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "status": "COMPLETED",
                "report_content": "# Sales Report for Acme Corp\n\n...",
                "report_markdown": "# Sales Report for Acme Corp\n\n...",
                "download_url": "https://storage.googleapis.com/bucket/report.md?signed=...",
                "model_card": {
                    "model_version": "gemini-3.5-flash",
                    "tokens_used": 28500,
                    "latency_seconds": 185.0,
                    "cost_usd": 0.35,
                },
            }
        }
    }


class ResearchJobListItem(BaseModel):
    """One entry from the research job history."""

    job_id: str = Field(..., description="Unique research job ID")
    status: str = Field(
        ..., description="Job status (QUEUED, PROCESSING, COMPLETED, FAILED, CANCELLED)"
    )
    company_name: str | None = Field(None, description="Researched company name")
    company: str | None = Field(None, description="Alias for company_name")
    account_id: str | None = Field(None, description="Account identifier")
    created_at: str | None = Field(
        None, description="Job creation timestamp (ISO 8601)"
    )
    completed_at: str | None = Field(
        None, description="Job completion timestamp (ISO 8601)"
    )
    error_message: str | None = Field(None, description="Error message if failed")
    progress: int | None = Field(None, description="Progress percentage (0-100)")

    def model_post_init(self, __context: Any) -> None:
        if self.company is None:
            self.company = self.company_name


class ResearchJobListResponse(BaseModel):
    """List response of research jobs for current user."""

    jobs: list[ResearchJobListItem] = Field(
        default_factory=list, description="List of research jobs"
    )


class ResearchCancelResponse(BaseModel):
    """Response model for job cancellation."""

    job_id: str = Field(..., description="Cancelled job ID")
    status: str = Field("CANCELLED", description="Cancelled status")
    message: str = Field(
        "Job cancelled successfully", description="Cancellation message"
    )


class ResearchFeedbackRequest(BaseModel):
    """Request model for submitting user feedback"""

    feedback: str = Field(
        ...,
        description="Feedback message",
        min_length=1,
        max_length=1000,
    )

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {"feedback": "Great and highly detailed report!"}
        },
    )


class ResearchFeedbackResponse(BaseModel):
    """Response model for feedback submission"""

    job_id: str = Field(..., description="Unique job ID")
    status: str = Field(..., description="Status of the feedback submission")
    message: str = Field(..., description="Success message")

    model_config = {
        "json_schema_extra": {
            "example": {
                "job_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "status": "SUCCESS",
                "message": "Feedback submitted successfully",
            }
        }
    }
