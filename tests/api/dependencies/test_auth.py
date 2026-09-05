from unittest.mock import MagicMock

import pytest

from src.api.core.security import AuthenticatedUser
from src.api.dependencies import (
    get_current_user,
    get_current_user_context,
    verify_token,
)


def _mock_request(headers: dict[str, str] | None = None) -> MagicMock:
    headers = headers or {}
    request = MagicMock()
    request.headers.get.side_effect = lambda key, default=None: headers.get(
        key, default
    )
    request.state = MagicMock()
    return request


@pytest.mark.asyncio
async def test_verify_token_delegates_to_authenticated_user():
    user = AuthenticatedUser(
        oid="oid-1",
        email="user@colt.net",
        roles=["SalesAgent.User"],
        business_unit="Sales",
        organization="Colt",
    )
    result = await verify_token(user)
    assert result is user


@pytest.mark.asyncio
async def test_get_current_user_returns_dict_shape():
    user = AuthenticatedUser(
        oid="oid-1",
        email="user@colt.net",
        roles=["SalesAgent.User"],
        business_unit="Sales",
        organization="Colt",
    )
    result = await get_current_user(user)
    assert result == {
        "oid": "oid-1",
        "email": "user@colt.net",
        "roles": ["SalesAgent.User"],
        "business_unit": "Sales",
        "organization": "Colt",
    }


@pytest.mark.asyncio
async def test_get_current_user_context_sets_request_state():
    user = AuthenticatedUser(
        oid="oid-1",
        email="user@colt.net",
        roles=["SalesAgent.User"],
        business_unit="Sales",
        organization="Colt",
    )
    request = _mock_request()
    context = await get_current_user_context(request, user)
    assert context is user
    assert request.state.user is user
