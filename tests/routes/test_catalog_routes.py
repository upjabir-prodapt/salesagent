from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from src.core.config import settings
from src.dependencies.auth import get_current_user
from src.dependencies.handler_dependencies import get_catalog_handler
from src.handlers.catalog_handler import CatalogHandler
from src.routes.app import app


@pytest.fixture
def catalog_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    service = MagicMock()
    service.get_status.return_value = {"chunks_path": "gs://chunks"}
    service.get_manifest.return_value = {"version": "1"}
    service.search.return_value = "no matches"
    service.get_job_status.return_value = {
        "job_id": "cat-1",
        "operation": "prepare",
        "status": "PENDING",
    }
    service.create_job.return_value = "cat-1"
    from pathlib import Path

    async def _save_pdf(content: bytes, filename: str) -> Path:
        return Path("/tmp") / filename

    service.save_uploaded_pdf = _save_pdf

    handler = CatalogHandler(service)
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_catalog_handler] = lambda: handler
    app.dependency_overrides[get_current_user] = lambda: {"email": "test@colt.net"}

    with TestClient(app) as client:
        client.mock_catalog = service
        yield client

    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def test_catalog_status_route(catalog_client: TestClient) -> None:
    response = catalog_client.get(f"{settings.API_PREFIX}/catalog/status")
    assert response.status_code == status.HTTP_200_OK


def test_catalog_manifest_route(catalog_client: TestClient) -> None:
    response = catalog_client.get(f"{settings.API_PREFIX}/catalog/manifest")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["version"] == "1"


def test_catalog_search_route(catalog_client: TestClient) -> None:
    response = catalog_client.post(
        f"{settings.API_PREFIX}/catalog/search",
        json={"query": "mpls"},
    )
    assert response.status_code == status.HTTP_200_OK


def test_catalog_get_job_route(catalog_client: TestClient) -> None:
    response = catalog_client.get(f"{settings.API_PREFIX}/catalog/jobs/cat-1")
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["job_id"] == "cat-1"


def test_catalog_create_job_route(catalog_client: TestClient) -> None:
    response = catalog_client.post(
        f"{settings.API_PREFIX}/catalog/jobs",
        data={"operation": "index_update", "version_id": "v1"},
        files={"pdf": ("doc.pdf", BytesIO(b"%PDF"), "application/pdf")},
    )
    assert response.status_code == status.HTTP_202_ACCEPTED
