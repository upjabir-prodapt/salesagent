"""Authentication Schemas - Pydantic Models for Auth Domain"""

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    """Request model for authentication (email derived from IAP JWT)."""

    email: str | None = Field(
        default=None,
        description="Deprecated — email is taken from verified IAP identity",
    )
    business_unit: str = Field(..., description="Business unit of the user")
    organization: str = Field(..., description="Organization of the user")

    model_config = {
        "json_schema_extra": {
            "example": {
                "business_unit": "Marketing",
                "organization": "Colt",
            }
        }
    }


class Token(BaseModel):
    """Response model for access token"""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(..., description="Token type (Bearer)")
    email: str = Field(..., description="Verified user email from IAP identity")


class WhoamiResponse(BaseModel):
    """Response model for IAP identity probe."""

    email: str = Field(..., description="Verified user email from IAP identity")
