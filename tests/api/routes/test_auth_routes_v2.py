import time
from datetime import timedelta
from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException, status

from src.api.core import security
from src.api.core.security import SESSION_COOKIE_NAME, create_access_token
from src.shared.config import settings

# SALES_REQUIRED_GROUP in tests/settings_env.py is "ai-salesagent-users".
DEV_IAP_HEADER = {
    "X-Dev-IAP-User-Email": "test@colt.net",
    "x-dev-iap-user-groups": "ai-salesagent-users",
}

BASE_CLAIMS = {
    "sub": "test@colt.net",
    "business_unit": "Sales",
    "organization": "Colt",
}


def _decode(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def _session_token(**overrides) -> str:
    """Mint a session token with sane defaults, overridable per test."""
    claims = {
        **BASE_CLAIMS,
        "auth_time": int(time.time()),
        "scopes": ["sales"],
        **overrides,
    }
    # `None` means "omit this claim entirely" -- distinct from an empty list,
    # which is what a user with no entitlements would legitimately carry.
    claims = {k: v for k, v in claims.items() if v is not None}
    return create_access_token(claims=claims, expires_delta=timedelta(minutes=30))


def _cleared(response) -> bool:
    set_cookie = response.headers.get("set-cookie", "")
    if SESSION_COOKIE_NAME not in set_cookie:
        return False
    return "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()


def test_auth_token_success(client):
    payload = {
        "business_unit": "Sales",
        "organization": "Colt",
    }
    response = client.post(
        f"{settings.API_PREFIX}/auth/token",
        headers=DEV_IAP_HEADER,
        json=payload,
    )
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"
    assert body["email"] == "test@colt.net"
    assert body.get("expires_in") == 1800
    assert "colt_session" in response.cookies


def test_auth_token_invalid_domain(client):
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    with patch("src.api.core.security.settings") as mock_s:
        mock_s.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"

        payload = {
            "business_unit": "Sales",
            "organization": "Colt",
        }
        response = client.post(
            f"{settings.API_PREFIX}/auth/token",
            headers={
                "X-Dev-IAP-User-Email": "test@example.com",
                "x-dev-iap-user-groups": "ai-salesagent-users",
            },
            json=payload,
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


def test_auth_token_rejects_missing_required_group(client):
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    with patch("src.api.core.security.settings") as mock_s:
        mock_s.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"

        payload = {
            "business_unit": "Sales",
            "organization": "Colt",
        }
        response = client.post(
            f"{settings.API_PREFIX}/auth/token",
            headers={"X-Dev-IAP-User-Email": "test@colt.net"},
            json=payload,
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


def test_whoami_returns_verified_email(client):
    response = client.get(
        f"{settings.API_PREFIX}/auth/whoami",
        headers=DEV_IAP_HEADER,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"email": "test@colt.net", "entitled": True}


def test_whoami_rejects_missing_required_group(client):
    response = client.get(
        f"{settings.API_PREFIX}/auth/whoami",
        headers={"X-Dev-IAP-User-Email": "test@colt.net"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN


# --- Minted claims -----------------------------------------------------------


def test_token_carries_auth_time_and_own_scope(client):
    before = int(time.time())
    response = client.post(
        f"{settings.API_PREFIX}/auth/token",
        headers=DEV_IAP_HEADER,
        json={"business_unit": "Sales", "organization": "Colt"},
    )
    assert response.status_code == status.HTTP_200_OK
    payload = _decode(response.json()["access_token"])
    assert payload["scopes"] == ["sales"]
    assert before <= payload["auth_time"] <= int(time.time())


def test_token_carries_translation_scope_when_entitled_to_both(client, monkeypatch):
    """The cookie is shared, so a dual-entitled user must get both scopes.

    If Sales-Agent stamped only its own scope, hubLogin()'s Promise.all would
    leave whichever response landed last in charge of the cookie and the other
    service would 403 on every call.
    """

    async def _mock_scopes(_identity):
        return {"sales", "translation"}

    monkeypatch.setattr("src.api.routes.auth.resolve_session_scopes", _mock_scopes)

    response = client.post(
        f"{settings.API_PREFIX}/auth/token",
        headers=DEV_IAP_HEADER,
        json={"business_unit": "Sales", "organization": "Colt"},
    )
    assert response.status_code == status.HTTP_200_OK
    payload = _decode(response.json()["access_token"])
    assert payload["scopes"] == ["sales", "translation"]


def test_expires_in_matches_token_exp(client):
    """`expires_in` is advertised, `exp` is enforced -- they must agree."""
    response = client.post(
        f"{settings.API_PREFIX}/auth/token",
        headers=DEV_IAP_HEADER,
        json={"business_unit": "Sales", "organization": "Colt"},
    )
    body = response.json()
    payload = _decode(body["access_token"])
    assert abs((payload["exp"] - payload["iat"]) - body["expires_in"]) <= 1


# --- Scope enforcement -------------------------------------------------------


@pytest.fixture
def real_auth_client(client):
    """`client` with its `get_current_user` stub removed.

    The shared fixture short-circuits authentication so route tests can focus
    on handler behaviour. Scope enforcement lives inside that very dependency,
    so exercising it requires letting the real one run.
    """
    from src.api.dependencies import get_current_user
    from src.api.main import app

    stub = app.dependency_overrides.pop(get_current_user)
    try:
        yield client
    finally:
        app.dependency_overrides[get_current_user] = stub
        client.cookies.clear()


def test_data_route_rejects_token_scoped_to_other_service_only(real_auth_client):
    """The cross-service bypass this claim exists to close."""
    real_auth_client.cookies.set(
        SESSION_COOKIE_NAME, _session_token(scopes=["translation"])
    )
    response = real_auth_client.get(f"{settings.API_PREFIX}/research/jobs")
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_data_route_accepts_token_with_own_scope(real_auth_client):
    real_auth_client.cookies.set(SESSION_COOKIE_NAME, _session_token())
    response = real_auth_client.get(f"{settings.API_PREFIX}/research/jobs")
    assert response.status_code == status.HTTP_200_OK


def test_legacy_token_without_scopes_allowed_while_lenient():
    assert settings.REQUIRE_SCOPE_CLAIM is False
    payload = security.decode_and_verify_token(_session_token(scopes=None))
    assert payload["sub"] == "test@colt.net"


def test_legacy_token_without_scopes_rejected_when_strict(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_SCOPE_CLAIM", True)
    with pytest.raises(HTTPException) as exc:
        security.decode_and_verify_token(_session_token(scopes=None))
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


def test_empty_scopes_rejected_even_while_lenient():
    """Leniency covers *absent* scopes only.

    A token that carries `scopes: []` was minted by a scope-aware service for
    a user with no entitlements -- honouring it would reopen the very hole the
    leniency window is meant to close gradually.
    """
    with pytest.raises(HTTPException) as exc:
        security.decode_and_verify_token(_session_token(scopes=[]))
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


# --- Refresh -----------------------------------------------------------------


def test_refresh_with_no_body_returns_new_token_and_cookie(client):
    """A body-less POST must not 422: the UI renews on a timer with nothing to send."""
    client.cookies.set(SESSION_COOKIE_NAME, _session_token())
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["email"] == "test@colt.net"
    assert body["expires_in"] == settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    assert SESSION_COOKIE_NAME in response.headers.get("set-cookie", "")


def test_refresh_preserves_auth_time_and_extends_expiry(client):
    original_auth_time = int(time.time()) - 3600
    # Deliberately short-lived, so "the new token outlives the old one" is
    # observable rather than two identical 30-minute expiries in the same
    # wall-clock second.
    token = create_access_token(
        claims={**BASE_CLAIMS, "auth_time": original_auth_time, "scopes": ["sales"]},
        expires_delta=timedelta(minutes=5),
    )
    old = _decode(token)

    client.cookies.set(SESSION_COOKIE_NAME, token)
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()

    assert response.status_code == status.HTTP_200_OK
    new = _decode(response.json()["access_token"])
    # Preserved, not restamped -- otherwise the absolute cap would slide
    # forward with every renewal and never be reached.
    assert new["auth_time"] == original_auth_time
    assert new["exp"] > old["exp"]
    assert new["sub"] == old["sub"]
    assert new["business_unit"] == old["business_unit"]
    assert new["organization"] == old["organization"]


def test_refresh_rejects_expired_token(client):
    token = create_access_token(
        claims={**BASE_CLAIMS, "auth_time": int(time.time()) - 60, "scopes": ["sales"]},
        expires_delta=timedelta(minutes=-5),
    )
    client.cookies.set(SESSION_COOKIE_NAME, token)
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_refresh_rejects_unauthenticated_caller(client):
    response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_refresh_past_absolute_cap_401s_and_clears_cookie(client):
    beyond_cap = int(time.time()) - (settings.SESSION_ABSOLUTE_MAX_MINUTES * 60) - 60
    client.cookies.set(SESSION_COOKIE_NAME, _session_token(auth_time=beyond_cap))
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert _cleared(response)


def test_refresh_falls_back_to_iat_for_legacy_token_past_cap(client):
    """Pre-`auth_time` tokens still get a ceiling, derived from `iat`."""
    token = create_access_token(
        claims={**BASE_CLAIMS, "scopes": ["sales"]},
        expires_delta=timedelta(minutes=30),
    )
    payload = _decode(token)
    payload["iat"] = (
        int(time.time()) - (settings.SESSION_ABSOLUTE_MAX_MINUTES * 60) - 60
    )
    forged = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)

    client.cookies.set(SESSION_COOKIE_NAME, forged)
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_refresh_403s_and_clears_cookie_when_entitlement_revoked(client, monkeypatch):
    """Revocation takes effect at the next renewal, not the next login."""
    monkeypatch.setattr(settings, "IS_LOCAL", False)

    async def _no_scopes(email: str) -> set[str]:
        assert email == "test@colt.net"
        return set()

    monkeypatch.setattr("src.api.routes.auth.resolve_scopes", _no_scopes)

    client.cookies.set(SESSION_COOKIE_NAME, _session_token())
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert _cleared(response)


def test_refresh_restamps_freshly_resolved_scopes(client, monkeypatch):
    """Scopes come from Firestore each time, so a newly granted service appears."""
    monkeypatch.setattr(settings, "IS_LOCAL", False)

    async def _both(_email: str) -> set[str]:
        return {"sales", "translation"}

    monkeypatch.setattr("src.api.routes.auth.resolve_scopes", _both)

    client.cookies.set(SESSION_COOKIE_NAME, _session_token(scopes=["sales"]))
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()

    assert response.status_code == status.HTTP_200_OK
    assert _decode(response.json()["access_token"])["scopes"] == [
        "sales",
        "translation",
    ]


def test_refresh_normalises_email_before_lookup(client, monkeypatch):
    monkeypatch.setattr(settings, "IS_LOCAL", False)
    seen: list[str] = []

    async def _record(email: str) -> set[str]:
        seen.append(email)
        return {"sales"}

    monkeypatch.setattr("src.api.routes.auth.resolve_scopes", _record)

    client.cookies.set(SESSION_COOKIE_NAME, _session_token(sub="  Test@Colt.NET "))
    try:
        response = client.post(f"{settings.API_PREFIX}/auth/refresh")
    finally:
        client.cookies.clear()

    assert response.status_code == status.HTTP_200_OK
    assert seen == ["test@colt.net"]


# --- Logout ------------------------------------------------------------------


def test_logout_clears_cookie_without_authentication(client):
    response = client.post(f"{settings.API_PREFIX}/auth/logout")
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert _cleared(response)
