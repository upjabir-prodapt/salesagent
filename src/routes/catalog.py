"""Catalog vector index API routes — thin HTTP adapters delegating to CatalogHandler."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, UploadFile, status

from ..core.config import settings
from ..dependencies.auth import get_current_user
from ..dependencies.handler_dependencies import get_catalog_handler
from ..handlers.catalog_handler import CatalogHandler
from ..models.catalog_schemas import (
    CatalogJobResponse,
    CatalogJobStatusResponse,
    CatalogSearchRequest,
    CatalogSearchResponse,
    CatalogStatusResponse,
)

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/catalog",
    tags=["catalog"],
)

CatalogHandlerDep = Annotated[CatalogHandler, Depends(get_catalog_handler)]


@router.get("/status", response_model=CatalogStatusResponse)
async def catalog_status(
    handler: CatalogHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return handler.get_status(current_user)


@router.get("/manifest")
async def catalog_manifest(
    handler: CatalogHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return handler.get_manifest(current_user)


@router.post("/search", response_model=CatalogSearchResponse)
async def catalog_search(
    body: CatalogSearchRequest,
    handler: CatalogHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return await handler.search(body, current_user)


@router.post(
    "/jobs",
    response_model=CatalogJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_catalog_job(
    background_tasks: BackgroundTasks,
    handler: CatalogHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
    operation: str = Form(...),
    version_id: str | None = Form(None),
    options_json: str | None = Form(None),
    pdf: UploadFile | None = None,
):
    return await handler.create_job(
        background_tasks=background_tasks,
        current_user=current_user,
        operation=operation,
        version_id=version_id,
        options_json=options_json,
        pdf=pdf,
    )


@router.get("/jobs/{job_id}", response_model=CatalogJobStatusResponse)
async def get_catalog_job(
    job_id: str,
    handler: CatalogHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    return handler.get_job(job_id, current_user)


@router.post(
    "/rebuild",
    response_model=CatalogJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rebuild_catalog(
    background_tasks: BackgroundTasks,
    handler: CatalogHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
    pdf: UploadFile = File(...),  # noqa: B008
    options_json: str | None = Form(None),
):
    return await handler.rebuild(
        background_tasks=background_tasks,
        current_user=current_user,
        pdf=pdf,
        options_json=options_json,
    )
