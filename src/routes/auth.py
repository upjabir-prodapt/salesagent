"""Authentication API Routes - Endpoints for auth operations."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from ..core.config import settings
from ..core.iap_auth import IapIdentity, require_sales_agent_entitlement
from ..core.logging_config import logger
from ..core.security import SESSION_COOKIE_NAME, create_access_token
from ..models.auth_schemas import AuthRequest, Token, WhoamiResponse

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/auth",
    tags=["auth"],
)

_require_sales_group = require_sales_agent_entitlement()


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
    response: Response,
    identity: Annotated[IapIdentity, Depends(_require_sales_group)],
):
    """Issue JWT using verified IAP identity and user-provided cost attribution.

    Sets the JWT as an httpOnly session cookie (primary, XSS-resistant
    transport), mirroring the Translation service, in addition to returning
    it in the response body for backward compatibility with clients still
    using the `x-app-auth` header during the migration window.
    """
    logger.info("Authentication attempt for user: %s", identity.email)

    access_token = create_access_token(
        claims={
            "sub": identity.email,
            "business_unit": request.business_unit.strip(),
            "organization": request.organization.strip(),
        }
    )

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=access_token,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=not settings.IS_LOCAL,
        samesite="strict",
        path="/",
    )

    logger.info("Authentication successful for user: %s", identity.email)
    return Token(access_token=access_token, token_type="bearer", email=identity.email)
