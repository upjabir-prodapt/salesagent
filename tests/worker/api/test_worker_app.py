"""Smoke tests for worker ASGI application (src/routes/worker_app.py)."""

from fastapi import status
from fastapi.testclient import TestClient

from src.worker.main import app


def test_worker_app_health():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] == "healthy"
        assert "Worker" in data["service"]


def test_worker_app_root():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["service"] == "sales-agent-worker"
        assert data["status"] == "running"


def test_worker_app_does_not_mount_public_routers():
    route_paths = [route.path for route in app.routes]
    assert "/internal/tasks/research" in route_paths
    assert "/health" in route_paths
    assert "/api/v1/research/initiate" not in route_paths
    assert "/api/v1/auth/token" not in route_paths
