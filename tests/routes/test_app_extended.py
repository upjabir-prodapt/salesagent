import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from src.routes.app import app

def test_root_endpoint_functional():
    """Functional test for root endpoint."""
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Professional Sales Intelligence Research API" in response.json()["description"]

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
    with patch("src.routes.app._init_bigquery", new_callable=AsyncMock) as mock_bq, \
         patch("src.routes.app._init_gcs", new_callable=AsyncMock) as mock_gcs, \
         patch("src.routes.app._init_telemetry") as mock_tel:
        
        with TestClient(app):
            # Lifespan should have triggered these
            assert mock_bq.called
            assert mock_gcs.called
            # telemetry depends on env var, but let's see if it's called
            assert mock_tel.called

def test_init_telemetry_functional():
    """Verify telemetry init logic when env var is set."""
    from src.routes.app import _init_telemetry
    with patch.dict("os.environ", {"OTEL_SERVICE_NAME": "test-service"}):
        with patch("src.routes.app.TracerProvider"), \
             patch("src.routes.app.BatchSpanProcessor"), \
             patch("src.routes.app.CloudTraceSpanExporter"), \
             patch("src.routes.app.GoogleGenAiSdkInstrumentor"), \
             patch("src.routes.app.VertexAIInstrumentor"), \
             patch("src.routes.app.SQLite3Instrumentor"):
            
            _init_telemetry()
            # If we reached here without error and it was invoked, good
