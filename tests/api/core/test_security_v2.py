from datetime import timedelta
from unittest.mock import patch

import jwt

from src.api.core.security import create_access_token


def test_create_access_token():
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    with patch("src.api.core.security.settings") as mock_s:
        mock_s.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"

        data = {"sub": "test@example.com"}
        token = create_access_token(data)
        assert token is not None

        payload = jwt.decode(token, secret, algorithms=["HS256"])
        assert payload["sub"] == "test@example.com"
        assert "exp" in payload


def test_create_access_token_with_delta():
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    with patch("src.api.core.security.settings") as mock_s:
        mock_s.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"

        data = {"sub": "test@example.com"}
        delta = timedelta(minutes=10)
        token = create_access_token(data, expires_delta=delta)
        assert token is not None

        payload = jwt.decode(token, secret, algorithms=["HS256"])
        assert payload["sub"] == "test@example.com"
