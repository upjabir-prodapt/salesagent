import pytest
from unittest.mock import patch
from fastapi import status
from src.core.config import settings

def test_auth_token_success(client):
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    with patch("src.core.security.settings") as mock_s:
        mock_s.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"
        
        payload = {
            "email": "test@colt.net",
            "business_unit": "Sales",
            "organization": "Colt"
        }
        response = client.post(f"{settings.API_PREFIX}/auth/token", json=payload)
        assert response.status_code == status.HTTP_200_OK
        assert "access_token" in response.json()
        assert response.json()["token_type"] == "bearer"

def test_auth_token_invalid_domain(client):
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    with patch("src.core.security.settings") as mock_s:
        mock_s.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"
        
        payload = {
            "email": "test@example.com",
            "business_unit": "Sales",
            "organization": "Colt"
        }
        response = client.post(f"{settings.API_PREFIX}/auth/token", json=payload)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        assert "Only @colt.net is allowed" in response.json()["detail"]
