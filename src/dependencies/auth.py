"""Authentication Dependencies for FastAPI Routes"""

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, status

from ..core.security import (
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    app_auth_scheme,
    decode_and_verify_token,
    extract_bearer_token,
)


async def verify_token(
    request: Request,
    api_key: Annotated[str | None, Security(app_auth_scheme)],
) -> dict[str, Any]:
    """Verify bearer token from x-app-auth or the session cookie.

    Accepts the JWT from either the legacy `x-app-auth` header or the
    httpOnly `colt_session` cookie (preferred, set by POST /api/v1/auth/token).
    The header takes priority so older/cached clients keep working during
    the migration window; once all clients are confirmed cookie-only, the
    header path can be removed as a cleanup pass.
    """
    token = extract_bearer_token(api_key) or extract_bearer_token(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Not authenticated. Set x-app-auth to the access_token from "
                "POST /api/v1/auth/token, or Bearer <access_token>."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_and_verify_token(token)


async def get_current_user(
    payload: Annotated[dict[str, Any], Depends(verify_token)],
) -> dict[str, Any]:
    """Backward-compatible dependency returning raw token payload with email mapping."""
    if "sub" in payload and "email" not in payload:
        payload["email"] = payload["sub"]
    return payload


async def get_current_user_context(
    request: Request,
    payload: Annotated[dict[str, Any], Depends(verify_token)],
) -> AuthenticatedUser:
    """FastAPI dependency to extract normalized user context from JWT."""
    user = AuthenticatedUser(
        email=str(payload["sub"]),
        business_unit=str(payload["business_unit"]),
        organization=str(payload["organization"]),
    )
    request.state.user = user
    return user
