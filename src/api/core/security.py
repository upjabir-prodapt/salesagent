"""Security utilities shared by API auth dependencies.

The HS256 session/cookie machinery (SESSION_COOKIE_NAME, app_auth_scheme,
create_access_token, decode_and_verify_token, _enforce_service_scope,
normalize_scopes) that used to live here has been deleted: Apigee is now the
sole authority for authentication and authorization (see
src/api/core/apigee_auth.py), and this service no longer mints or verifies
its own session tokens.
"""

from pydantic import BaseModel


def extract_bearer_token(header_value: str | None) -> str | None:
    """Parse a header value: raw token or ``Bearer <token>`` (case-insensitive)."""
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
    """Authenticated user context extracted from Apigee-injected headers.

    `oid`, `email`, and `roles` are re-derived by Apigee from the verified
    Entra JWT and cannot be forged by the caller. `business_unit` and
    `organization` pass through from the BFF unchanged and are NOT
    independently verified.
    """

    oid: str
    email: str
    roles: list[str] = []
    business_unit: str
    organization: str
