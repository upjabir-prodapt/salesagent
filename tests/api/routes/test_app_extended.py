from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


def test_root_endpoint_functional():
    """Functional test for root endpoint."""
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert (
            "Professional Sales Intelligence Research API"
            in response.json()["description"]
        )


def test_health_check_endpoint_functional():
    """Functional test for health endpoint."""
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_lifespan_initialization_functional():
    """Verify functional lifespan initialization flow."""
    # We use TestClient as a context manager to trigger lifespan
    with (
        patch("src.api.main._init_bigquery", new_callable=AsyncMock) as mock_bq,
        patch("src.api.main._init_gcs", new_callable=AsyncMock) as mock_gcs,
        patch("src.api.main._init_telemetry") as mock_tel,
        TestClient(app),
    ):
        # Lifespan should have triggered these
        assert mock_bq.called
        assert mock_gcs.called
        # telemetry depends on env var, but let's see if it's called
        assert mock_tel.called


def test_init_telemetry_functional():
    """Verify ADK-style telemetry bootstrap path is invoked."""
    from src.api.main import _init_telemetry

    with (
        patch("src.api.main.google.auth.default", return_value=("cred", "proj")),
        patch("src.api.main.get_gcp_exporters") as mock_get_exporters,
        patch("src.api.main.get_gcp_resource") as mock_get_resource,
        patch("src.api.main.maybe_set_otel_providers") as mock_set_providers,
        patch(
            "src.api.main.FastAPIInstrumentor.instrument_app"
        ) as mock_fastapi_instrument,
        patch("src.api.main.GoogleGenAiSdkInstrumentor") as mock_genai,
        patch("src.api.main.settings.OTEL_ENABLED", True),
    ):
        mock_get_exporters.return_value = "hooks"
        mock_get_resource.return_value = "resource"

        _init_telemetry(app)

        mock_get_exporters.assert_called_once()
        mock_get_resource.assert_called_once_with("proj")
        mock_set_providers.assert_called_once()
        mock_fastapi_instrument.assert_called_once_with(app)
        mock_genai.return_value.instrument.assert_called_once()
