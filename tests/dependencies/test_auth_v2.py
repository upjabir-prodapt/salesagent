from unittest.mock import patch

import jwt
import pytest
from fastapi import HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials

from src.dependencies.auth import get_current_user


@pytest.mark.asyncio
async def test_get_current_user_success():
    secret = "very-secret-key-that-is-at-least-32-chars-long"

    payload = {"sub": "user@colt.net", "business_unit": "Sales", "organization": "Colt"}
    token = jwt.encode(payload, secret, algorithm="HS256")
    auth_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with patch("src.core.security.settings") as mock_s:
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"
        user = await get_current_user(auth_creds)
        assert user["sub"] == "user@colt.net"
        assert user["business_unit"] == "Sales"


@pytest.mark.asyncio
async def test_get_current_user_missing_sub():
    secret = "very-secret-key-that-is-at-least-32-chars-long"
    payload = {"business_unit": "Sales", "organization": "Colt"}
    token = jwt.encode(payload, secret, algorithm="HS256")
    auth_creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with patch("src.core.security.settings") as mock_s:
        mock_s.SECRET_KEY = secret
        mock_s.ALGORITHM = "HS256"
        with pytest.raises(HTTPException) as excinfo:
            await get_current_user(auth_creds)
        assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_current_user_invalid_token():
    auth_creds = HTTPAuthorizationCredentials(
        scheme="Bearer", credentials="invalid-token"
    )

    with patch("src.core.security.settings") as mock_s:
        mock_s.SECRET_KEY = "key"
        mock_s.ALGORITHM = "HS256"
        with pytest.raises(HTTPException) as excinfo:
            await get_current_user(auth_creds)
        assert excinfo.value.status_code == status.HTTP_401_UNAUTHORIZED
