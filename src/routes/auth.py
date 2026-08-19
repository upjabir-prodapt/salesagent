"""Authentication API Routes - Endpoints for auth operations."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..core.config import settings
from ..core.iap_auth import IapIdentity, require_group
from ..core.logging_config import logger
from ..core.security import create_access_token
from ..models.auth_schemas import AuthRequest, Token, WhoamiResponse

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/auth",
    tags=["auth"],
)

_require_sales_group = require_group(settings.SALES_REQUIRED_GROUP)


@router.get("/whoami", response_model=WhoamiResponse)
async def whoami(
    identity: Annotated[IapIdentity, Depends(_require_sales_group)],
) -> WhoamiResponse:
    """Return verified user email + entitlement status.

    Raises 403 (via the require_group dependency) when the user is not a member
    of the Entra security group configured in SALES_REQUIRED_GROUP.
    """
    return WhoamiResponse(email=identity.email, entitled=True)


@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: AuthRequest,
    identity: Annotated[IapIdentity, Depends(_require_sales_group)],
):
    """Issue JWT using verified IAP identity and user-provided cost attribution."""
    logger.info("Authentication attempt for user: %s", identity.email)

    access_token = create_access_token(
        claims={
            "sub": identity.email,
            "business_unit": request.business_unit.strip(),
            "organization": request.organization.strip(),
        }
    )

    logger.info("Authentication successful for user: %s", identity.email)
    return Token(access_token=access_token, token_type="bearer", email=identity.email)
