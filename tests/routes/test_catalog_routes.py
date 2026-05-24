"""Tests for catalog API routes."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.dependencies.auth import get_current_user
from src.dependencies.handler_dependencies import get_catalog_handler
from src.handlers.catalog_handler import CatalogHandler
from src.routes.app import app


@pytest.fixture
def catalog_client():
    mock_service = MagicMock()
    mock_service.get_status.return_value = {
        "active_version": "63ad7f0b",
        "index_vector_count": 32,
        "index_deployed": True,
        "manifest_updated_at": "2026-05-24T00:00:00+00:00",
        "chunks_path": "colt-product-catalog/current/chunks.json",
    }
    mock_service.get_manifest.return_value = {"active_version": "63ad7f0b"}
    mock_service.search.return_value = "Match 1"
    mock_service.create_job.return_value = "cat_test-job"
    mock_service.save_uploaded_pdf = AsyncMock(
        return_value=Path("/tmp/catalog.pdf")
    )
    mock_service.get_job_status.return_value = {
        "job_id": "cat_test-job",
        "operation": "rebuild",
        "status": "PENDING",
        "progress": 0,
        "current_step": "Queued",
        "version_id": None,
        "error_message": None,
        "user_email": "user@test.com",
        "created_at": None,
        "updated_at": None,
        "metadata": {},
    }

    mock_handler = CatalogHandler(mock_service)
    app.dependency_overrides[get_catalog_handler] = lambda: mock_handler
    app.dependency_overrides[get_current_user] = lambda: {
        "email": "user@test.com",
        "business_unit": "Sales",
        "organization": "Acme",
    }
    with TestClient(app) as client:
        client.mock_service = mock_service
        yield client
    app.dependency_overrides.clear()


def test_catalog_status(catalog_client):
    response = catalog_client.get("/api/v1/catalog/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active_version"] == "63ad7f0b"
    catalog_client.mock_service.get_status.assert_called_once()


def test_catalog_search(catalog_client):
    response = catalog_client.post(
        "/api/v1/catalog/search",
        json={"query": "SD-WAN"},
    )
    assert response.status_code == 200
    assert response.json()["results"] == "Match 1"


def test_catalog_rebuild_requires_pdf(catalog_client):
    response = catalog_client.post("/api/v1/catalog/rebuild")
    assert response.status_code == 422


def test_catalog_rebuild_accepted(catalog_client):
    response = catalog_client.post(
        "/api/v1/catalog/rebuild",
        files={"pdf": ("catalog.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["job_id"] == "cat_test-job"
    assert body["status"] == "PENDING"
    catalog_client.mock_service.create_job.assert_called_once()
    catalog_client.mock_service.save_uploaded_pdf.assert_called_once()


def test_catalog_job_get(catalog_client):
    response = catalog_client.get("/api/v1/catalog/jobs/cat_test-job")
    assert response.status_code == 200
    assert response.json()["operation"] == "rebuild"


def test_catalog_job_missing_version(catalog_client):
    response = catalog_client.post(
        "/api/v1/catalog/jobs",
        data={"operation": "publish"},
    )
    assert response.status_code == 400


def test_catalog_job_prepare_requires_pdf(catalog_client):
    response = catalog_client.post(
        "/api/v1/catalog/jobs",
        data={"operation": "prepare"},
    )
    assert response.status_code == 400
