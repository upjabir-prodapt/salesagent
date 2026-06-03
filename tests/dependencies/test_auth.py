import pytest
from fastapi import HTTPException

from src.core.security import create_access_token
from src.dependencies.auth import (
    get_current_user,
    get_current_user_context,
    verify_token,
)


@pytest.mark.asyncio
async def test_verify_token_missing_header():
    with pytest.raises(HTTPException) as exc_info:
        await verify_token(None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_token_and_user_context(mock_settings):
    token = create_access_token(
        {
            "sub": "user@colt.net",
            "business_unit": "Sales",
            "organization": "Colt",
        }
    )
    payload = await verify_token(f"Bearer {token}")
    user = await get_current_user(payload)
    assert user["email"] == "user@colt.net"

    context = await get_current_user_context(payload)
    assert context.email == "user@colt.net"
    assert context.business_unit == "Sales"
