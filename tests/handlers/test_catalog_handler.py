from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks, HTTPException, UploadFile

from src.core.exceptions import ResourceNotFoundError
from src.handlers.catalog_handler import CatalogHandler
from src.models.catalog_schemas import CatalogSearchRequest


@pytest.fixture
def handler() -> CatalogHandler:
    service = MagicMock()
    service.get_status.return_value = {
        "chunks_path": "gs://bucket/chunks",
        "active_version": "v1",
    }
    service.get_manifest.return_value = {"entries": []}
    service.search.return_value = "product results"
    service.get_job_status.return_value = {
        "job_id": "cat-1",
        "operation": "prepare",
        "status": "COMPLETED",
    }
    service.create_job.return_value = "cat-job-1"
    service.save_uploaded_pdf = AsyncMock(return_value=Path("/tmp/test.pdf"))
    service.process_job_background = MagicMock()
    return CatalogHandler(service)


@pytest.fixture
def user() -> dict:
    return {"email": "user@colt.net"}


def test_get_status(handler: CatalogHandler, user: dict) -> None:
    response = handler.get_status(user)
    assert response.active_version == "v1"
    handler._service.get_status.assert_called_once()


def test_get_manifest_success(handler: CatalogHandler, user: dict) -> None:
    assert handler.get_manifest(user) == {"entries": []}


def test_get_manifest_not_found(handler: CatalogHandler, user: dict) -> None:
    handler._service.get_manifest.side_effect = FileNotFoundError("missing")

    with pytest.raises(HTTPException) as exc:
        handler.get_manifest(user)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_search(handler: CatalogHandler, user: dict) -> None:
    body = CatalogSearchRequest(query="mpls")
    response = await handler.search(body, user)

    assert response.query == "mpls"
    assert response.results == "product results"


@pytest.mark.asyncio
async def test_create_job_requires_pdf_for_prepare(
    handler: CatalogHandler, user: dict
) -> None:
    with pytest.raises(HTTPException) as exc:
        await handler.create_job(
            background_tasks=BackgroundTasks(),
            current_user=user,
            operation="prepare",
            version_id=None,
            options_json=None,
            pdf=None,
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_job_publish_requires_version_id(
    handler: CatalogHandler, user: dict
) -> None:
    pdf = UploadFile(filename="doc.pdf", file=BytesIO(b"%PDF"))

    with pytest.raises(HTTPException) as exc:
        await handler.create_job(
            background_tasks=BackgroundTasks(),
            current_user=user,
            operation="publish",
            version_id=None,
            options_json=json.dumps({"skip_publish": True}),
            pdf=pdf,
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_job_accepts_operation(
    handler: CatalogHandler, user: dict
) -> None:
    pdf = UploadFile(filename="doc.pdf", file=BytesIO(b"%PDF"))
    tasks = BackgroundTasks()

    response = await handler.create_job(
        background_tasks=tasks,
        current_user=user,
        operation="index_update",
        version_id="v-42",
        options_json=None,
        pdf=pdf,
    )

    assert response.job_id == "cat-job-1"
    assert response.operation == "index_update"
    handler._service.create_job.assert_called_once()


def test_get_job_found(handler: CatalogHandler, user: dict) -> None:
    response = handler.get_job("cat-1", user)
    assert response.job_id == "cat-1"


def test_get_job_not_found(handler: CatalogHandler, user: dict) -> None:
    handler._service.get_job_status.return_value = None

    with pytest.raises(ResourceNotFoundError):
        handler.get_job("missing", user)


@pytest.mark.asyncio
async def test_rebuild_requires_pdf(handler: CatalogHandler, user: dict) -> None:
    empty = UploadFile(filename="", file=BytesIO(b""))
    handler._service.save_uploaded_pdf = AsyncMock(return_value=None)

    with pytest.raises(HTTPException) as exc:
        await handler.rebuild(
            background_tasks=BackgroundTasks(),
            current_user=user,
            pdf=empty,
            options_json=None,
        )

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_rebuild_accepts_empty_options_json(
    handler: CatalogHandler, user: dict
) -> None:
    pdf = UploadFile(filename="doc.pdf", file=BytesIO(b"%PDF"))

    response = await handler.rebuild(
        background_tasks=BackgroundTasks(),
        current_user=user,
        pdf=pdf,
        options_json="",
    )

    assert response.operation == "rebuild"
    handler._service.create_job.assert_called_once()


@pytest.mark.asyncio
async def test_rebuild_starts_job(handler: CatalogHandler, user: dict) -> None:
    pdf = UploadFile(filename="doc.pdf", file=BytesIO(b"%PDF"))

    response = await handler.rebuild(
        background_tasks=BackgroundTasks(),
        current_user=user,
        pdf=pdf,
        options_json=json.dumps({"deploy_after": True}),
    )

    assert response.operation == "rebuild"
    handler._service.create_job.assert_called_once()
