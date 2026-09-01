"""Authentication API Routes - Endpoints for auth operations."""

import time
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

from src.api.core.entitlements import resolve_scopes
from src.api.core.iap_auth import (
    IapIdentity,
    require_sales_agent_entitlement,
    resolve_session_scopes,
)
from src.api.core.security import (
    SERVICE_SCOPE,
    SESSION_COOKIE_NAME,
    create_access_token,
)
from src.api.dependencies import verify_token
from src.api.schemas.auth_schemas import AuthRequest, Token, WhoamiResponse
from src.shared.config import settings
from src.shared.logging_config import logger

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/auth",
    tags=["auth"],
)

_require_sales_group = require_sales_agent_entitlement()


def _set_session_cookie(response: Response, token: str, max_age: int) -> None:
    """Write the shared `colt_session` cookie.

    Attributes must stay byte-identical to Translation's, and to every other
    place this cookie is written or deleted here: the two services share one
    cookie, so any divergence in path/samesite/secure produces a second,
    shadowing cookie rather than an overwrite.
    """
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=not settings.IS_LOCAL,
        samesite="strict",
        path="/",
    )


def _delete_session_cookie(response: Response) -> None:
    """Clear the shared `colt_session` cookie using the same attributes."""
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        secure=not settings.IS_LOCAL,
        samesite="strict",
        path="/",
    )


def _clearing_headers() -> dict[str, str]:
    """Set-Cookie header that clears the session, for use on an HTTPException.

    Raising discards whatever was written to the injected `Response`, because
    the exception handler builds a fresh one. When a refresh is refused the
    session is definitively over, so the cookie must be cleared on the way
    out -- otherwise the browser keeps re-presenting a credential that can
    only ever be rejected, and the UI has no signal to stop retrying.
    """
    scratch = Response()
    _delete_session_cookie(scratch)
    return {"set-cookie": scratch.headers["set-cookie"]}


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

    Two claims beyond the identity ones are stamped here:

    * `auth_time` -- when this human actually authenticated with IAP. It is
      copied forward unchanged by every subsequent renewal, so it is what
      bounds the session's absolute lifetime no matter how many times the
      token is re-minted.
    * `scopes` -- which services the session may reach. Both services' scopes
      are resolved and stamped, because this one cookie is shared with
      Translation.
    """
    logger.info("Authentication attempt for user: %s", identity.email)

    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    scopes = await resolve_session_scopes(identity)
    access_token = create_access_token(
        claims={
            "sub": identity.email,
            "business_unit": request.business_unit.strip(),
            "organization": request.organization.strip(),
            "auth_time": int(time.time()),
            "scopes": sorted(scopes),
        },
        # Passed explicitly rather than relying on create_access_token's
        # default, so the lifetime this endpoint advertises in `expires_in`
        # and the one actually baked into `exp` cannot drift apart.
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    _set_session_cookie(response, access_token, expires_in)

    logger.info("Authentication successful for user: %s", identity.email)
    return Token(
        access_token=access_token,
        token_type="bearer",  # noqa: S106
        expires_in=expires_in,
        email=identity.email,
    )


@router.post("/refresh", response_model=Token)
async def refresh_access_token(
    response: Response,
    payload: Annotated[dict[str, Any], Depends(verify_token)],
):
    """Slide the session forward, re-minting from the still-valid current token.

    Deliberately takes NO request body: the UI renews on a timer and has
    nothing to send, and declaring a Pydantic body parameter would make a
    body-less POST fail validation with 422 instead of succeeding.

    Authentication is the caller's existing `colt_session` cookie (or
    `x-app-auth` header), validated by the usual `verify_token` dependency.
    An already-expired token therefore 401s here and cannot be renewed --
    that is the intended limit of a sliding session with no refresh token:
    renewal only works while the current token still lives.

    Two things are re-checked on every renewal, which is what makes a 30
    minute token acceptable in the first place:

    * the absolute cap, from the preserved `auth_time`; and
    * the Firestore entitlement, so a revoked user loses access within one
      token lifetime rather than at their next login.
    """
    now = int(time.time())
    # Legacy tokens predate `auth_time`; `iat` is the closest honest stand-in
    # and errs toward ending the session sooner, never later.
    auth_time = payload.get("auth_time") or payload.get("iat")
    if auth_time is None:
        logger.warning("Refresh denied: token carries neither auth_time nor iat")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session cannot be renewed. Please sign in again.",
            headers=_clearing_headers(),
        )

    auth_time = int(auth_time)
    if now - auth_time > settings.SESSION_ABSOLUTE_MAX_MINUTES * 60:
        logger.info("Refresh denied: session exceeded absolute maximum lifetime")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please sign in again.",
            headers=_clearing_headers(),
        )

    email = str(payload["sub"]).strip().lower()

    if settings.IS_LOCAL:
        # No live Firestore locally; carry the existing scopes forward so the
        # renewal path is exercisable offline.
        scopes = set(payload.get("scopes") or [SERVICE_SCOPE])
    else:
        scopes = await resolve_scopes(email)
        if SERVICE_SCOPE not in scopes:
            logger.warning(
                "Refresh denied: entitlement no longer grants %s", SERVICE_SCOPE
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this service. Contact your administrator.",
                headers=_clearing_headers(),
            )

    expires_in = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    access_token = create_access_token(
        claims={
            "sub": payload["sub"],
            "business_unit": payload["business_unit"],
            "organization": payload["organization"],
            # Preserved, never restamped -- restamping it here would turn the
            # absolute cap into another sliding window and remove the ceiling.
            "auth_time": auth_time,
            "scopes": sorted(scopes),
        },
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    _set_session_cookie(response, access_token, expires_in)

    return Token(
        access_token=access_token,
        token_type="bearer",  # noqa: S106
        expires_in=expires_in,
        email=str(payload["sub"]),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Clear the shared session cookie.

    Unauthenticated by design: the only effect is removing a credential, so
    requiring a valid one would just make logout fail exactly when it is most
    needed (an expired or malformed session the user wants to be rid of).
    """
    _delete_session_cookie(response)
