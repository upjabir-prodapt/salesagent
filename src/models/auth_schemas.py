"""Authentication Schemas - Pydantic Models for Auth Domain"""

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    """Request model for authentication"""

    email: str = Field(..., description="User email address")
    business_unit: str = Field(..., description="Business unit of the user")
    organization: str = Field(..., description="Organization of the user")

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "john.doe@colt.net",
                "business_unit": "Marketing",
                "organization": "Colt",
            }
        }
    }


class Token(BaseModel):
    """Response model for access token"""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(..., description="Token type (Bearer)")
