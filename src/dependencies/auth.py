"""Authentication Dependencies for FastAPI Routes"""

from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from ..core.config import settings
from ..core.security import (
    AuthenticatedUser,
    decode_and_verify_token,
    security,
)


async def verify_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
) -> dict[str, Any]:
    """Verify bearer token and return JWT payload."""
    if not settings.AUTH_ENABLED:
        # Explicit auth bypass for local/dev environments.
        return {
            "sub": "local-dev-user@colt.net",
            "email": "local-dev-user@colt.net",
            "business_unit": "Engineering",
            "organization": "Local",
            "auth_mode": "bypass",
        }

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_and_verify_token(credentials.credentials)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
) -> dict[str, Any]:
    """Backward-compatible dependency returning raw token payload with email mapping."""
    payload = await verify_token(credentials)
    if "sub" in payload and "email" not in payload:
        payload["email"] = payload["sub"]
    return payload


async def get_current_user_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),  # noqa: B008
) -> AuthenticatedUser:
    """FastAPI dependency to extract normalized user context from JWT."""
    payload = await verify_token(credentials)
    return AuthenticatedUser(
        email=str(payload["sub"]),
        business_unit=str(payload["business_unit"]),
        organization=str(payload["organization"]),
    )
