from typing import Any

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """Standard error response model."""

    error: str = Field(..., description="Error title or code")
    detail: str = Field(..., description="Detailed error description")
    request_id: str | None = Field(
        None, description="Request ID associated with the error"
    )
    timestamp: str | None = Field(None, description="Timestamp of the error")
    metadata: dict[str, Any] | None = Field(None, description="Additional context")

    model_config = {
        "json_schema_extra": {
            "example": {
                "error": "Validation Failed",
                "detail": "Company name cannot be empty.",
                "request_id": "123e4567-e89b-12d3-a456-426614174000",
                "timestamp": "2023-10-27T10:00:00Z",
                "metadata": {"field": "company_name"},
            }
        }
    }
