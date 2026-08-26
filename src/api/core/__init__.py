"""API core auth and security utilities."""

from .entitlements import has_sales_agent_access
from .iap_auth import (
    IapIdentity,
    get_iap_identity,
    get_iap_user,
    require_sales_agent_entitlement,
)
from .security import (
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    app_auth_scheme,
    create_access_token,
    decode_and_verify_token,
    extract_bearer_token,
)

__all__ = [
    "AuthenticatedUser",
    "IapIdentity",
    "SESSION_COOKIE_NAME",
    "app_auth_scheme",
    "create_access_token",
    "decode_and_verify_token",
    "extract_bearer_token",
    "get_iap_identity",
    "get_iap_user",
    "has_sales_agent_access",
    "require_sales_agent_entitlement",
]
