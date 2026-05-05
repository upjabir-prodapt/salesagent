"""Research Schemas - Pydantic Models for Research Domain"""

from pydantic import BaseModel, Field


class ResearchInitiateRequest(BaseModel):
    """Request model for initiating research"""

    account_id: str = Field(
        ..., 
        description="Account identifier", 
        min_length=2,
        max_length=50
    )
    company_name: str = Field(
        ..., 
        description="Company name to research", 
        min_length=2, 
        max_length=100,
        pattern=r"^[a-zA-Z0-9\s\&\.\-\',]+$"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "account_id": "ACC-123",
                "company_name": "Acme Corp"
            }
        }
    }


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
    status: str = Field(
        ..., description="Current status: PENDING, PROCESSING, COMPLETED, FAILED"
    )
    progress: int = Field(..., description="Progress percentage (0-100)")
    current_step: str | None = Field(
        None, description="Human-readable current processing step"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "request_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "status": "PROCESSING",
                "progress": 45,
                "current_step": "Strategy Agent: Analyzing Annual Report",
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
    status: str = Field(..., description="Final status")
    report_content: str | None = Field(None, description="Full markdown report content")
    download_url: str | None = Field(
        None, description="Signed GCS URL for downloading the report"
    )
    model_card: ModelCard | None = Field(None, description="Model and cost metadata")

    model_config = {
        "json_schema_extra": {
            "example": {
                "request_id": "job_123e4567-e89b-12d3-a456-426614174000",
                "status": "COMPLETED",
                "report_content": "# Sales Report for Acme Corp\n\n...",
                "download_url": "https://storage.googleapis.com/bucket/report.md?signed=...",
                "model_card": {
                    "model_version": "gemini-2.5-pro",
                    "tokens_used": 28500,
                    "latency_seconds": 185.0,
                    "cost_usd": 0.35,
                },
            }
        }
    }
