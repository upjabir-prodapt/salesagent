"""Security utilities for authentication and authorization."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from fastapi import HTTPException, status
from fastapi.security import APIKeyHeader
from jwt import InvalidTokenError
from pydantic import BaseModel

from src.api.core.entitlements import SCOPE_SALES
from src.shared.config import settings

logger = logging.getLogger(__name__)

# The scope this service requires in every session token it accepts.
SERVICE_SCOPE = SCOPE_SALES

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
    """Create an HS256 signed JWT access token.

    LOAD-BEARING: `settings.SECRET_KEY` is intentionally IDENTICAL to
    Translation's `JWT_SECRET_KEY`. One IAP login mints one `colt_session`
    cookie that both services accept, and that only works while both sign and
    verify with the same key. Rotating one side independently breaks
    cross-service SSO immediately and silently -- every call to the un-rotated
    service starts 401ing. Rotate both together, or not at all. What keeps the
    shared secret safe is the `scopes` claim (see _enforce_service_scope), not
    key separation.
    """
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


def normalize_scopes(raw: Any) -> set[str]:
    """Normalize a `scopes` claim into a lowercase set.

    Accepts the list form this service mints as well as a space/comma
    separated string, so a token produced by a differently-serialising client
    is still understood rather than silently treated as unscoped.
    """
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
        return {p.lower() for p in parts}
    if isinstance(raw, (list, tuple, set)):
        return {str(s).strip().lower() for s in raw if str(s).strip()}
    return set()


def _enforce_service_scope(payload: dict[str, Any]) -> None:
    """Require this service's own scope in the token's `scopes` claim.

    Sales-Agent and Translation share one HS256 secret and one `colt_session`
    cookie so that a single IAP login covers both. A valid signature therefore
    proves only "some Colt service minted this", not "the caller is entitled
    to *this* service" -- this check is what actually separates the two.

    While `REQUIRE_SCOPE_CLAIM` is False, a token with no `scopes` claim at
    all is accepted so sessions minted before scopes existed keep working
    through the rollout window. A token that *does* carry `scopes` is always
    held to them, in both modes -- otherwise the bypass would remain wide open
    for exactly the tokens the claim was added to constrain.
    """
    if "scopes" not in payload:
        if settings.REQUIRE_SCOPE_CLAIM:
            logger.warning("Token has no scopes claim and REQUIRE_SCOPE_CLAIM is on")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this service. Contact your administrator.",
            )
        return

    scopes = normalize_scopes(payload.get("scopes"))
    if SERVICE_SCOPE not in scopes:
        logger.warning(
            "Token rejected: scope %r absent from token scopes %s",
            SERVICE_SCOPE,
            sorted(scopes),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this service. Contact your administrator.",
        )


def decode_and_verify_token(token: str) -> dict[str, Any]:
    """Decode and verify JWT token, including required claims and scope."""
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

    _enforce_service_scope(payload)

    return payload
