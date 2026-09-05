"""Apigee-based authentication for the Sales-Agent API.

Apigee is the sole authority for authentication and authorization in this
pipeline: browser -> aihub-bff (Entra login/IAP) -> Apigee `int` proxy ->
this Cloud Run service. All IAP-JWT / session-cookie / Firestore entitlement
auth that used to live in this service has been deleted.

Two independent things are verified here:

1. The caller itself: Apigee authenticates to this Cloud Run service with a
   Google-signed ID token whose audience is this service's own Cloud Run
   URL. Apigee's default `GoogleIDToken` target-server auth policy sends
   this in the `Authorization` header; an earlier design doc assumed
   `X-Serverless-Authorization` instead. Which one Apigee actually populates
   has not been empirically confirmed yet, so both are accepted and neither
   is required on its own -- whichever is present is used, and which one
   was used is logged. This mirrors the Cloud Tasks OIDC verification
   pattern in src/worker/api/auth.py (same `id_token.verify_oauth2_token` +
   expected-service-account-email check).
2. The end user's identity: once the caller is verified, Apigee-injected
   `x-colt-user-*` headers carry the real user's identity. `oid`, `email`,
   and `roles` are re-derived by Apigee from the verified Entra JWT and
   cannot be forged; `department` and `company` pass through from the BFF
   unchanged and are NOT independently verified.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request, status
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token

from src.api.core.security import AuthenticatedUser, extract_bearer_token
from src.shared.config import settings

logger = logging.getLogger(__name__)

# Neither header is authoritative on its own -- see module docstring.
SERVERLESS_AUTH_HEADER = "X-Serverless-Authorization"
AUTHORIZATION_HEADER = "Authorization"

# Canonical Entra/BFF user-context headers Apigee injects on every request
# that reaches this service (decided contract, do not rename).
HEADER_USER_OID = "x-colt-user-oid"
HEADER_USER_EMAIL = "x-colt-user-email"
HEADER_USER_ROLES = "x-colt-user-roles"
HEADER_USER_DEPARTMENT = "x-colt-user-department"
HEADER_USER_COMPANY = "x-colt-user-company"

# Fixed local-dev identity used when no x-colt-user-* headers are present at
# all -- there is no Apigee (and often no BFF) in front of a local run.
LOCAL_DEV_OID = "local-dev"
LOCAL_DEV_EMAIL = "local-dev@example.com"
LOCAL_DEV_ROLES = ["SalesAgent.User"]


def _read_google_id_token(request: Request) -> tuple[str, str] | None:
    """Read the Apigee-supplied Google ID token.

    Prefers `X-Serverless-Authorization`, falls back to `Authorization`.
    Returns (token, header_name_used) or None if neither header carried a
    token.
    """
    for header in (SERVERLESS_AUTH_HEADER, AUTHORIZATION_HEADER):
        token = extract_bearer_token(request.headers.get(header))
        if token:
            return token, header
    return None


def _verify_apigee_caller(request: Request) -> dict[str, Any]:
    """Verify the Google-signed ID token Apigee uses to call this Cloud Run service.

    Raises 401 when the token is missing or fails verification, 403 when it
    verifies but the `email` claim does not match the expected Apigee
    runtime service account.
    """
    found = _read_google_id_token(request)
    if found is None:
        logger.warning(
            "Missing Apigee Google ID token on %s %s (checked %s and %s headers)",
            request.method,
            request.url.path,
            SERVERLESS_AUTH_HEADER,
            AUTHORIZATION_HEADER,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Apigee identity token",
        )
    token, header_used = found
    logger.info(
        "Apigee Google ID token supplied via %s header on %s %s",
        header_used,
        request.method,
        request.url.path,
    )

    try:
        claims = id_token.verify_oauth2_token(
            token,
            google_auth_requests.Request(),
            audience=settings.CLOUD_RUN_SERVICE_URL,
        )
    except Exception as exc:
        logger.warning("Apigee ID token verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Apigee identity token",
        ) from exc

    expected_sa = settings.APIGEE_RUNTIME_SA_EMAIL.strip()
    email = (claims.get("email") or "").strip()
    if expected_sa and email != expected_sa:
        logger.warning(
            "Apigee runtime service account mismatch: got %s expected %s",
            email,
            expected_sa,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unexpected caller service account",
        )

    return claims


def _user_from_headers(request: Request) -> AuthenticatedUser:
    """Build the normalized user context from Apigee-injected headers.

    Raises 401 when `x-colt-user-oid` is missing -- every other user header
    degrades gracefully to an empty string rather than crashing.
    """
    oid = request.headers.get(HEADER_USER_OID)
    if not oid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing {HEADER_USER_OID} header",
        )

    email = request.headers.get(HEADER_USER_EMAIL) or ""
    raw_roles = request.headers.get(HEADER_USER_ROLES) or ""
    roles = [role.strip() for role in raw_roles.split(",") if role.strip()]
    business_unit = request.headers.get(HEADER_USER_DEPARTMENT) or ""
    organization = request.headers.get(HEADER_USER_COMPANY) or ""

    return AuthenticatedUser(
        oid=oid,
        email=email,
        roles=roles,
        business_unit=business_unit,
        organization=organization,
    )


def _local_dev_user(request: Request) -> AuthenticatedUser:
    """Local-dev identity: real x-colt-user-* headers when present, else a fixed fake identity."""
    if request.headers.get(HEADER_USER_OID):
        return _user_from_headers(request)
    logger.info(
        "IS_LOCAL: no %s header present, using fixed local-dev identity",
        HEADER_USER_OID,
    )
    return AuthenticatedUser(
        oid=LOCAL_DEV_OID,
        email=LOCAL_DEV_EMAIL,
        roles=list(LOCAL_DEV_ROLES),
        business_unit="",
        organization="",
    )


async def get_authenticated_user(request: Request) -> AuthenticatedUser:
    """FastAPI dependency: verify the Apigee caller, then extract the end user's identity.

    Local dev (`IS_LOCAL=true`) skips Google ID-token verification entirely
    -- there is no Apigee in front of a local run -- and builds the user
    context straight from headers (or a fixed fake identity).
    """
    if settings.IS_LOCAL:
        return _local_dev_user(request)

    _verify_apigee_caller(request)
    return _user_from_headers(request)
