"""Catalog vector index API handlers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, HTTPException, UploadFile, status
from opentelemetry import trace

from ..core.exceptions import ResourceNotFoundError
from ..core.logging_config import contextualize, logger
from ..models.catalog_schemas import (
    CatalogJobOptions,
    CatalogJobResponse,
    CatalogJobStatusResponse,
    CatalogRebuildOptions,
    CatalogSearchRequest,
    CatalogSearchResponse,
    CatalogStatusResponse,
)
from ..services.catalog import CatalogService

_PDF_REQUIRED_OPERATIONS = frozenset({"prepare", "rebuild"})


class CatalogHandler:
    """Maps catalog HTTP operations to CatalogService."""

    def __init__(self, service: CatalogService) -> None:
        self._service = service

    def get_status(self, current_user: dict[str, Any]) -> CatalogStatusResponse:
        with contextualize(user_email=current_user["email"]):
            return CatalogStatusResponse(**self._service.get_status())

    def get_manifest(self, current_user: dict[str, Any]) -> dict:
        with contextualize(user_email=current_user["email"]):
            try:
                return self._service.get_manifest()
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Manifest not found: {exc}",
                ) from exc

    async def search(
        self, body: CatalogSearchRequest, current_user: dict[str, Any]
    ) -> CatalogSearchResponse:
        with contextualize(user_email=current_user["email"]):
            results = await asyncio.to_thread(self._service.search, body.query)
            return CatalogSearchResponse(query=body.query, results=results)

    async def create_job(
        self,
        *,
        background_tasks: BackgroundTasks,
        current_user: dict[str, Any],
        operation: str,
        version_id: str | None,
        options_json: str | None,
        pdf: UploadFile | None,
    ) -> CatalogJobResponse:
        opts = CatalogJobOptions()
        if options_json:
            opts = CatalogJobOptions(**json.loads(options_json))

        pdf_path = await self._save_uploaded_pdf(pdf)
        if operation in _PDF_REQUIRED_OPERATIONS and pdf_path is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"pdf file upload is required for operation '{operation}'",
            )

        return await self._start_job(
            background_tasks=background_tasks,
            current_user=current_user,
            operation=operation,
            version_id=version_id,
            options=opts,
            pdf_path=pdf_path,
        )

    def get_job(
        self, job_id: str, current_user: dict[str, Any]
    ) -> CatalogJobStatusResponse:
        with contextualize(user_email=current_user["email"]):
            row = self._service.get_job_status(job_id)
            if not row:
                raise ResourceNotFoundError(f"Catalog job {job_id} not found")
            return CatalogJobStatusResponse(**row)

    async def rebuild(
        self,
        *,
        background_tasks: BackgroundTasks,
        current_user: dict[str, Any],
        pdf: UploadFile,
        options_json: str | None,
    ) -> CatalogJobResponse:
        opts = CatalogRebuildOptions()
        if options_json:
            opts = CatalogRebuildOptions(**json.loads(options_json))

        pdf_path = await self._save_uploaded_pdf(pdf)
        if pdf_path is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pdf file upload is required for rebuild",
            )

        job_options = CatalogJobOptions(
            skip_publish=False,
            skip_index_update=opts.skip_index_update,
            deploy_after=opts.deploy_after,
            deploy_force=opts.deploy_force,
            complete_overwrite=opts.complete_overwrite,
        )
        return await self._start_job(
            background_tasks=background_tasks,
            current_user=current_user,
            operation="rebuild",
            version_id=None,
            options=job_options,
            pdf_path=pdf_path,
        )

    async def _save_uploaded_pdf(self, pdf: UploadFile | None) -> Path | None:
        if pdf is None or not pdf.filename:
            return None
        content = await pdf.read()
        return await self._service.save_uploaded_pdf(content, pdf.filename)

    async def _start_job(
        self,
        *,
        background_tasks: BackgroundTasks,
        current_user: dict[str, Any],
        operation: str,
        version_id: str | None,
        options: CatalogJobOptions,
        pdf_path: Path | None,
    ) -> CatalogJobResponse:
        if operation in ("publish", "index_update", "index_create") and not version_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"version_id is required for operation '{operation}'",
            )

        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("catalog.job.accepted") as span:
            span.set_attribute("catalog.operation", operation)
            job_id = self._service.create_job(
                operation,
                current_user["email"],
                version_id=version_id,
                metadata={"options": options.model_dump()},
            )
            span.set_attribute("catalog.job_id", job_id)

            background_tasks.add_task(
                self._service.process_job_background,
                job_id,
                operation,
                current_user["email"],
                version_id=version_id,
                pdf_path=pdf_path,
                options=options,
            )
            logger.info("Accepted catalog job %s operation=%s", job_id, operation)
            return CatalogJobResponse(
                job_id=job_id,
                operation=operation,
                status="PENDING",
            )
