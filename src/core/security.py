"""Security utilities for authentication and authorization."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status
from fastapi.security import APIKeyHeader
from jwt import InvalidTokenError
from pydantic import BaseModel

from ..core.config import settings

logger = logging.getLogger(__name__)

# App JWT via x-app-auth (Cloud Run IAM uses Authorization / X-Serverless-Authorization).
# Use Security(app_auth_scheme) on routes/deps so Swagger shows the Authorize control.
app_auth_scheme = APIKeyHeader(
    name="x-app-auth",
    auto_error=False,
    description=(
        "JWT from POST /api/v1/auth/token. "
        "Paste access_token only, or use Bearer <access_token>."
    ),
)

# httpOnly session cookie name — preferred credential transport, mirroring
# the Translation service. The `x-app-auth` header above is kept as a
# fallback during the migration window; see dependencies/auth.py's
# verify_token, which reads whichever of the two is present.
SESSION_COOKIE_NAME = "colt_session"  # noqa: S105


def extract_bearer_token(header_value: str | None) -> str | None:
    """Parse x-app-auth: raw JWT or ``Bearer <jwt>`` (case-insensitive)."""
    if not header_value:
        return None
    value = header_value.strip()
    if not value:
        return None
    if value.lower().startswith("bearer "):
        token = value[7:].strip()
        return token or None
    return value


class AuthenticatedUser(BaseModel):
    """Authenticated user context extracted from JWT claims."""

    email: str
    business_unit: str
    organization: str


def create_access_token(
    claims: dict[str, Any], expires_delta: timedelta | None = None
) -> str:
    """Create an HS256 signed JWT access token."""
    now = datetime.now(UTC)
    expires = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        **claims,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_and_verify_token(token: str) -> dict[str, Any]:
    """Decode and verify JWT token, including required claims."""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    required_claims = ("sub", "business_unit", "organization")
    missing = [claim for claim in required_claims if not payload.get(claim)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing required token claims: {', '.join(missing)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload
