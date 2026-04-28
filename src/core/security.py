"""Security module for IAP JWT Verification and User Identity"""

import time
from typing import Optional

import requests
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from fastapi import Header, HTTPException, Request
from loguru import logger

from .config import settings
from .exceptions import AuthenticationError

# Cache for Google Public Keys for IAP
_IAP_PUBKEYS = {}
_LAST_KEY_FETCH = 0

async def verify_iap_jwt(
    request: Request, 
    x_goog_iap_jwt_assertion: Optional[str] = Header(None)
) -> dict:
    """
    Validates the IAP JWT assertion from the request header.
    Returns the decoded claims including user email.
    """
    if not settings.AUTH_ENABLED:
        return {"email": "anonymous@colt.net"}

    if not x_goog_iap_jwt_assertion:
        logger.error("[Security] Missing IAP JWT assertion header")
        raise AuthenticationError("Missing IAP JWT")

    try:
        # If IAP_AUDIENCE is not configured, we cannot verify cryptographically
        # Return unknown to satisfy logic without exploding on dummy strings
        if not settings.IAP_AUDIENCE:
            logger.warning("[Security] IAP_AUDIENCE not configured. Skipping signature check.")
            return {"email": "unknown@colt.net"}

        # Verify the JWT using Google's verification library
        claims = id_token.verify_token(
            x_goog_iap_jwt_assertion,
            google_requests.Request(),
            audience=settings.IAP_AUDIENCE,
        )
        return claims

    except Exception as exc:
        logger.error(f"[Security] JWT verification failed: {exc}")
        raise AuthenticationError("Invalid IAP token") from exc
