from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException, status

from src.core.security import extract_bearer_token
from src.dependencies.auth import get_current_user, verify_token


def test_extract_bearer_token():
    assert extract_bearer_token(None) is None
    assert extract_bearer_token("  ") is None
    assert extract_bearer_token("abc.jwt.here") == "abc.jwt.here"
    assert extract_bearer_token("Bearer abc.jwt.here") == "abc.jwt.here"
    assert extract_bearer_token("bearer abc.jwt.here") == "abc.jwt.here"


@pytest.mark.asyncio
async def test_get_current_user_success():
    secret = "very-secret-key-that-is-at-least-32-chars-long"

    payload = {"sub": "user@colt.net", "business_unit": "Sales", "organization": "Colt"}
    token = jwt.encode(payload, secret, algorithm="HS256")

    with patch("src.core.security.settings") as mock_s:
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"
        for app_auth in (f"Bearer {token}", token):
            user = await get_current_user(await verify_token(app_auth))
            assert user["sub"] == "user@colt.net"
            assert user["business_unit"] == "Sales"


@pytest.mark.asyncio
async def test_get_current_user_missing_sub():
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    payload = {"business_unit": "Sales", "organization": "Colt"}
    token = jwt.encode(payload, secret, algorithm="HS256")
    app_auth = f"Bearer {token}"

    with patch("src.core.security.settings") as mock_s:
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"
        with pytest.raises(HTTPException) as excinfo:
            await verify_token(app_auth)
        assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_invalid_token():
    with patch("src.core.security.settings") as mock_s:
        mock_s.SECRET_KEY = "key"
        mock_s.ALGORITHM = "HS256"
        with pytest.raises(HTTPException) as excinfo:
            await verify_token("Bearer invalid-token")
        assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_missing_header():
    with pytest.raises(HTTPException) as excinfo:
        await verify_token(None)
    assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED
