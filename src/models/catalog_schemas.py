"""Pydantic schemas for catalog vector API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CatalogOperation = Literal[
    "prepare",
    "publish",
    "index_update",
    "index_deploy",
    "index_create",
    "rebuild",
]


class CatalogJobOptions(BaseModel):
    skip_publish: bool = False
    skip_index_update: bool = False
    deploy_after: bool = False
    deploy_force: bool = False
    complete_overwrite: bool = True


class CatalogJobCreateRequest(BaseModel):
    operation: CatalogOperation
    version_id: str | None = None
    options: CatalogJobOptions = Field(default_factory=CatalogJobOptions)


class CatalogJobResponse(BaseModel):
    job_id: str
    operation: str
    status: str
    message: str = "Job accepted"


class CatalogJobStatusResponse(BaseModel):
    job_id: str
    operation: str
    status: str
    progress: int | None = None
    current_step: str | None = None
    version_id: str | None = None
    error_message: str | None = None
    user_email: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CatalogStatusResponse(BaseModel):
    active_version: str | None = None
    index_vector_count: int | None = None
    index_deployed: bool = False
    manifest_updated_at: str | None = None
    chunks_path: str


class CatalogSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


class CatalogSearchResponse(BaseModel):
    query: str
    results: str


class CatalogRebuildOptions(BaseModel):
    skip_index_update: bool = False
    deploy_after: bool = False
    deploy_force: bool = False
    complete_overwrite: bool = True
