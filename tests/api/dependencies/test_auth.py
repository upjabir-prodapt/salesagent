from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from src.api.core.security import SESSION_COOKIE_NAME, create_access_token
from src.api.dependencies import (
    get_current_user,
    get_current_user_context,
    verify_token,
)


@pytest.mark.asyncio
async def test_verify_token_missing_header():
    request = MagicMock(cookies={})
    with pytest.raises(HTTPException) as exc_info:
        await verify_token(request, None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_token_from_header(mock_settings):
    token = create_access_token(
        {
            "sub": "user@colt.net",
            "business_unit": "Sales",
            "organization": "Colt",
        }
    )
    request = MagicMock(cookies={})
    payload = await verify_token(request, f"Bearer {token}")
    user = await get_current_user(payload)
    assert user["email"] == "user@colt.net"

    context = await get_current_user_context(request, payload)
    assert context.email == "user@colt.net"
    assert context.business_unit == "Sales"
    assert request.state.user == context


@pytest.mark.asyncio
async def test_verify_token_from_cookie(mock_settings):
    token = create_access_token(
        {
            "sub": "user@colt.net",
            "business_unit": "Sales",
            "organization": "Colt",
        }
    )
    request = MagicMock(cookies={SESSION_COOKIE_NAME: token})
    payload = await verify_token(request, None)
    user = await get_current_user(payload)
    assert user["email"] == "user@colt.net"
