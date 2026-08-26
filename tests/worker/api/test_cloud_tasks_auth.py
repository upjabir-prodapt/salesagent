"""Tests for Cloud Tasks OIDC token verification."""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.worker.api.auth import require_cloud_tasks_oidc


@pytest.mark.asyncio
async def test_require_cloud_tasks_oidc_skip_flag():
    request = MagicMock()
    with patch("src.worker.api.auth.settings") as mock_settings:
        mock_settings.WORKER_SKIP_OIDC_VERIFICATION = True
        claims = await require_cloud_tasks_oidc(request)
        assert claims == {"skipped": True}


@pytest.mark.asyncio
async def test_require_cloud_tasks_oidc_missing_bearer():
    request = MagicMock()
    request.headers.get.return_value = ""
    with patch("src.worker.api.auth.settings") as mock_settings:
        mock_settings.WORKER_SKIP_OIDC_VERIFICATION = False
        with pytest.raises(HTTPException) as exc_info:
            await require_cloud_tasks_oidc(request)
        assert exc_info.value.status_code == 401
        assert "Missing Bearer token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_cloud_tasks_oidc_missing_audience():
    request = MagicMock()
    request.headers.get.return_value = "Bearer mock-token"
    with patch("src.worker.api.auth.settings") as mock_settings:
        mock_settings.WORKER_SKIP_OIDC_VERIFICATION = False
        mock_settings.WORKER_OIDC_AUDIENCE = ""
        mock_settings.CLOUD_TASKS_WORKER_URL = ""
        with pytest.raises(HTTPException) as exc_info:
            await require_cloud_tasks_oidc(request)
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_require_cloud_tasks_oidc_invalid_token():
    request = MagicMock()
    request.headers.get.return_value = "Bearer mock-token"
    with (
        patch("src.worker.api.auth.settings") as mock_settings,
        patch(
            "src.worker.api.auth.id_token.verify_oauth2_token",
            side_effect=Exception("token expired"),
        ),
    ):
        mock_settings.WORKER_SKIP_OIDC_VERIFICATION = False
        mock_settings.WORKER_OIDC_AUDIENCE = "https://worker.run.app"
        with pytest.raises(HTTPException) as exc_info:
            await require_cloud_tasks_oidc(request)
        assert exc_info.value.status_code == 401
        assert "Invalid OIDC token" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_cloud_tasks_oidc_service_account_mismatch():
    request = MagicMock()
    request.headers.get.return_value = "Bearer mock-token"
    with (
        patch("src.worker.api.auth.settings") as mock_settings,
        patch(
            "src.worker.api.auth.id_token.verify_oauth2_token",
            return_value={"email": "wrong-sa@project.iam.gserviceaccount.com"},
        ),
    ):
        mock_settings.WORKER_SKIP_OIDC_VERIFICATION = False
        mock_settings.WORKER_OIDC_AUDIENCE = "https://worker.run.app"
        mock_settings.CLOUD_TASKS_OIDC_SERVICE_ACCOUNT = (
            "expected-sa@project.iam.gserviceaccount.com"
        )
        with pytest.raises(HTTPException) as exc_info:
            await require_cloud_tasks_oidc(request)
        assert exc_info.value.status_code == 403
        assert "Unexpected service account" in exc_info.value.detail


@pytest.mark.asyncio
async def test_require_cloud_tasks_oidc_success():
    request = MagicMock()
    request.headers.get.return_value = "Bearer mock-token"
    expected_claims = {
        "email": "expected-sa@project.iam.gserviceaccount.com",
        "sub": "123",
    }
    with (
        patch("src.worker.api.auth.settings") as mock_settings,
        patch(
            "src.worker.api.auth.id_token.verify_oauth2_token",
            return_value=expected_claims,
        ),
    ):
        mock_settings.WORKER_SKIP_OIDC_VERIFICATION = False
        mock_settings.WORKER_OIDC_AUDIENCE = "https://worker.run.app"
        mock_settings.CLOUD_TASKS_OIDC_SERVICE_ACCOUNT = (
            "expected-sa@project.iam.gserviceaccount.com"
        )
        claims = await require_cloud_tasks_oidc(request)
        assert claims == expected_claims
