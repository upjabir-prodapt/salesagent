"""Tests for Apigee-based authentication (src/api/core/apigee_auth.py)."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.core import apigee_auth


def _request(headers: dict[str, str] | None = None) -> MagicMock:
    """Build a mock Request whose .headers.get() reads from a plain dict."""
    data = headers or {}
    request = MagicMock()
    request.headers.get.side_effect = lambda key, default=None: data.get(key, default)
    request.method = "GET"
    request.url.path = "/api/sales/v1/research/jobs"
    return request


FULL_USER_HEADERS = {
    "x-colt-user-oid": "entra-oid-123",
    "x-colt-user-email": "user@colt.net",
    "x-colt-user-roles": "SalesAgent.User,SalesAgent.Admin",
    "x-colt-user-department": "Sales",
    "x-colt-user-company": "Colt",
}


@pytest.mark.asyncio
async def test_local_dev_no_headers_uses_fixed_identity():
    with patch.object(apigee_auth, "settings") as mock_settings:
        mock_settings.IS_LOCAL = True
        user = await apigee_auth.get_authenticated_user(_request())

    assert user.oid == apigee_auth.LOCAL_DEV_OID
    assert user.email == apigee_auth.LOCAL_DEV_EMAIL
    assert user.roles == apigee_auth.LOCAL_DEV_ROLES
    assert user.business_unit == ""
    assert user.organization == ""


@pytest.mark.asyncio
async def test_local_dev_with_headers_uses_real_headers():
    with patch.object(apigee_auth, "settings") as mock_settings:
        mock_settings.IS_LOCAL = True
        user = await apigee_auth.get_authenticated_user(_request(FULL_USER_HEADERS))

    assert user.oid == "entra-oid-123"
    assert user.email == "user@colt.net"
    assert user.roles == ["SalesAgent.User", "SalesAgent.Admin"]
    assert user.business_unit == "Sales"
    assert user.organization == "Colt"


@pytest.mark.asyncio
async def test_success_via_serverless_authorization_header():
    headers = {
        **FULL_USER_HEADERS,
        "X-Serverless-Authorization": "Bearer good-token",
    }
    with (
        patch.object(apigee_auth, "settings") as mock_settings,
        patch(
            "src.api.core.apigee_auth.id_token.verify_oauth2_token",
            return_value={"email": "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"},
        ) as mock_verify,
    ):
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_RUN_SERVICE_URL = "https://sales-agent-api.run.app"
        mock_settings.APIGEE_RUNTIME_SA_EMAIL = (
            "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"
        )
        user = await apigee_auth.get_authenticated_user(_request(headers))

    mock_verify.assert_called_once()
    assert mock_verify.call_args.kwargs["audience"] == "https://sales-agent-api.run.app"
    assert user.oid == "entra-oid-123"
    assert user.email == "user@colt.net"
    assert user.roles == ["SalesAgent.User", "SalesAgent.Admin"]
    assert user.business_unit == "Sales"
    assert user.organization == "Colt"


@pytest.mark.asyncio
async def test_success_falls_back_to_authorization_header():
    headers = {**FULL_USER_HEADERS, "Authorization": "Bearer good-token"}
    with (
        patch.object(apigee_auth, "settings") as mock_settings,
        patch(
            "src.api.core.apigee_auth.id_token.verify_oauth2_token",
            return_value={"email": "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"},
        ),
    ):
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_RUN_SERVICE_URL = "https://sales-agent-api.run.app"
        mock_settings.APIGEE_RUNTIME_SA_EMAIL = (
            "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"
        )
        user = await apigee_auth.get_authenticated_user(_request(headers))

    assert user.oid == "entra-oid-123"


@pytest.mark.asyncio
async def test_missing_id_token_raises_401():
    with patch.object(apigee_auth, "settings") as mock_settings:
        mock_settings.IS_LOCAL = False
        with pytest.raises(HTTPException) as exc_info:
            await apigee_auth.get_authenticated_user(_request(FULL_USER_HEADERS))

    assert exc_info.value.status_code == 401
    assert "Missing Apigee identity token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_invalid_id_token_raises_401():
    headers = {**FULL_USER_HEADERS, "Authorization": "Bearer bad-token"}
    with (
        patch.object(apigee_auth, "settings") as mock_settings,
        patch(
            "src.api.core.apigee_auth.id_token.verify_oauth2_token",
            side_effect=Exception("token expired"),
        ),
    ):
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_RUN_SERVICE_URL = "https://sales-agent-api.run.app"
        with pytest.raises(HTTPException) as exc_info:
            await apigee_auth.get_authenticated_user(_request(headers))

    assert exc_info.value.status_code == 401
    assert "Invalid Apigee identity token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_wrong_service_account_raises_403():
    headers = {**FULL_USER_HEADERS, "Authorization": "Bearer good-token"}
    with (
        patch.object(apigee_auth, "settings") as mock_settings,
        patch(
            "src.api.core.apigee_auth.id_token.verify_oauth2_token",
            return_value={"email": "someone-else@project.iam.gserviceaccount.com"},
        ),
    ):
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_RUN_SERVICE_URL = "https://sales-agent-api.run.app"
        mock_settings.APIGEE_RUNTIME_SA_EMAIL = (
            "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"
        )
        with pytest.raises(HTTPException) as exc_info:
            await apigee_auth.get_authenticated_user(_request(headers))

    assert exc_info.value.status_code == 403
    assert "Unexpected caller service account" in exc_info.value.detail


@pytest.mark.asyncio
async def test_missing_oid_header_raises_401():
    headers = {
        "Authorization": "Bearer good-token",
        "x-colt-user-email": "user@colt.net",
    }
    with (
        patch.object(apigee_auth, "settings") as mock_settings,
        patch(
            "src.api.core.apigee_auth.id_token.verify_oauth2_token",
            return_value={"email": "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"},
        ),
    ):
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_RUN_SERVICE_URL = "https://sales-agent-api.run.app"
        mock_settings.APIGEE_RUNTIME_SA_EMAIL = (
            "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"
        )
        with pytest.raises(HTTPException) as exc_info:
            await apigee_auth.get_authenticated_user(_request(headers))

    assert exc_info.value.status_code == 401
    assert "x-colt-user-oid" in exc_info.value.detail


@pytest.mark.asyncio
async def test_department_and_company_absent_default_to_empty_string():
    headers = {
        "Authorization": "Bearer good-token",
        "x-colt-user-oid": "entra-oid-123",
        "x-colt-user-email": "user@colt.net",
        "x-colt-user-roles": "SalesAgent.User",
    }
    with (
        patch.object(apigee_auth, "settings") as mock_settings,
        patch(
            "src.api.core.apigee_auth.id_token.verify_oauth2_token",
            return_value={"email": "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"},
        ),
    ):
        mock_settings.IS_LOCAL = False
        mock_settings.CLOUD_RUN_SERVICE_URL = "https://sales-agent-api.run.app"
        mock_settings.APIGEE_RUNTIME_SA_EMAIL = (
            "apigee-int-runtime@gclt-aicoe-dev-apigee.iam.gserviceaccount.com"
        )
        user = await apigee_auth.get_authenticated_user(_request(headers))

    assert user.business_unit == ""
    assert user.organization == ""
    assert user.oid == "entra-oid-123"
    assert user.roles == ["SalesAgent.User"]
