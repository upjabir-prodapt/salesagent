"""Authentication API Routes - Endpoints for auth operations."""

from typing import Annotated

from fastapi import APIRouter, Depends

from ..core.config import settings
from ..core.iap_auth import get_iap_user
from ..core.logging_config import logger
from ..core.security import create_access_token
from ..models.auth_schemas import AuthRequest, Token, WhoamiResponse

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/auth",
    tags=["auth"],
)


@router.get("/whoami", response_model=WhoamiResponse)
async def whoami(
    iap_email: Annotated[str, Depends(get_iap_user)],
) -> WhoamiResponse:
    """Return verified user email from IAP JWT (entitlement probe)."""
    return WhoamiResponse(email=iap_email)


@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: AuthRequest,
    iap_email: Annotated[str, Depends(get_iap_user)],
):
    """Issue JWT using verified IAP identity and user-provided cost attribution."""
    logger.info("Authentication attempt for user: %s", iap_email)

    access_token = create_access_token(
        claims={
            "sub": iap_email,
            "business_unit": request.business_unit.strip(),
            "organization": request.organization.strip(),
        }
    )

    logger.info("Authentication successful for user: %s", iap_email)
    return Token(access_token=access_token, token_type="bearer", email=iap_email)
