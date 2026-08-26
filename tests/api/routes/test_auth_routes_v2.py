from unittest.mock import patch

from fastapi import status

from src.shared.config import settings

# SALES_REQUIRED_GROUP in tests/settings_env.py is "ai-salesagent-users".
DEV_IAP_HEADER = {
    "X-Dev-IAP-User-Email": "test@colt.net",
    "x-dev-iap-user-groups": "ai-salesagent-users",
}


def test_auth_token_success(client):
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
