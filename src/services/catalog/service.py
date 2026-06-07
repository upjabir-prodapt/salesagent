"""Catalog vector index operations — job orchestration and pipeline execution."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from typing import Any

from google.cloud import storage
from opentelemetry import trace

from ...core.config import settings
from ...core.exceptions import ServiceError
from ...core.logging_config import contextualize, logger
from ...models.catalog_schemas import CatalogJobOptions, CatalogOperation
from ...repositories.catalog_job_repository import CatalogJobRepository
from .pipeline import VectorCatalogPipeline
from .search import colt_product_search
from .vertex import VertexIndexManager

tracer = trace.get_tracer(__name__)


class CatalogService:
    def __init__(
        self,
        job_repository: CatalogJobRepository | None = None,
        pipeline: VectorCatalogPipeline | None = None,
    ) -> None:
        self.job_repo = job_repository or CatalogJobRepository()
        self.pipeline = pipeline or VectorCatalogPipeline(
            settings,
            storage_client=storage.Client(project=settings.GOOGLE_CLOUD_PROJECT),
        )

    def new_job_id(self) -> str:
        return f"{settings.CATALOG_JOB_ID_PREFIX}{uuid.uuid4()}"

    def create_job(
        self,
        operation: CatalogOperation,
        user_email: str,
        *,
        version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        job_id = self.new_job_id()
        self.job_repo.create_job(
            job_id,
            operation,
            user_email,
            version_id=version_id,
            metadata=metadata,
        )
        return job_id

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        return self.job_repo.get_job(job_id)

    def get_status(self) -> dict[str, Any]:
        manager = VertexIndexManager(settings)
        manifest: dict[str, Any] = {}
        active_version: str | None = None
        manifest_updated_at: str | None = None
        try:
            manifest = self.pipeline.fetch_manifest()
            active_version = manifest.get("active_version")
            manifest_updated_at = manifest.get("updated_at")
        except Exception as exc:
            logger.warning("Could not load catalog manifest: %s", exc)

        index_vector_count: int | None = None
        index_deployed = False
        try:
            index_vector_count = manager.vector_count()
            index_deployed = manager.is_deployed()
        except Exception as exc:
            logger.warning("Could not read Vertex index status: %s", exc)

        return {
            "active_version": active_version,
            "index_vector_count": index_vector_count,
            "index_deployed": index_deployed,
            "manifest_updated_at": manifest_updated_at,
            "chunks_path": settings.vector_search_catalog_chunks_blob,
        }

    def get_manifest(self) -> dict[str, Any]:
        return self.pipeline.fetch_manifest()

    def search(self, query: str) -> str:
        return colt_product_search(query)

    async def save_uploaded_pdf(self, upload_bytes: bytes, filename: str) -> Path:
        tmp_dir = settings.VECTOR_SEARCH_LOCAL_BUILD_DIR / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=tmp_dir
        ) as tmp:
            tmp.write(upload_bytes)
            return Path(tmp.name)

    def require_uploaded_pdf(self, uploaded_path: Path | None) -> Path:
        if uploaded_path is None:
            raise ValueError("Catalog PDF upload is required for this operation")
        path = uploaded_path.resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Uploaded catalog PDF not found: {path}")
        return path

    async def process_job_background(
        self,
        job_id: str,
        operation: CatalogOperation,
        user_email: str,
        *,
        version_id: str | None = None,
        pdf_path: Path | None = None,
        options: CatalogJobOptions | None = None,
    ) -> None:
        opts = options or CatalogJobOptions()
        with tracer.start_as_current_span("catalog.background.process") as span:
            span.set_attribute("catalog.job_id", job_id)
            span.set_attribute("catalog.operation", operation)
            with contextualize(user_email=user_email, trace_id=job_id):
                try:
                    self.job_repo.update_job(
                        job_id,
                        status="PROCESSING",
                        progress=5,
                        current_step="Starting",
                    )
                    await asyncio.to_thread(
                        self._run_operation,
                        job_id,
                        operation,
                        version_id=version_id,
                        pdf_path=pdf_path,
                        options=opts,
                    )
                    self.job_repo.update_job(
                        job_id,
                        status="COMPLETED",
                        progress=100,
                        current_step="Completed",
                    )
                except Exception as exc:
                    logger.exception("Catalog job %s failed", job_id)
                    self.job_repo.update_job(
                        job_id,
                        status="FAILED",
                        error_message=str(exc),
                        current_step="Failed",
                    )

    def _run_operation(
        self,
        job_id: str,
        operation: CatalogOperation,
        *,
        version_id: str | None,
        pdf_path: Path | None,
        options: CatalogJobOptions,
    ) -> None:
        if operation == "prepare":
            self._run_prepare(job_id, pdf_path)
        elif operation == "publish":
            self._run_publish(job_id, version_id, pdf_path)
        elif operation == "index_update":
            self._run_index_update(job_id, version_id, options.complete_overwrite)
        elif operation == "index_deploy":
            self._run_index_deploy(options.deploy_force)
        elif operation == "index_create":
            self._run_index_create(version_id)
        elif operation == "rebuild":
            self._run_rebuild(job_id, pdf_path, options)
        else:
            raise ServiceError(f"Unknown operation: {operation}")

    def _run_prepare(self, job_id: str, pdf_path: Path | None) -> None:
        self.job_repo.update_job(job_id, current_step="Chunking", progress=20)
        path = self.require_uploaded_pdf(pdf_path)
        artifacts, _ = self.pipeline.prepare(path)
        self.job_repo.update_job(
            job_id,
            version_id=artifacts.version_id,
            progress=80,
            current_step="Prepared",
        )

    def _run_publish(
        self, job_id: str, version_id: str | None, pdf_path: Path | None
    ) -> None:
        if not version_id:
            raise ValueError("version_id is required for publish")
        self.job_repo.update_job(job_id, current_step="Publishing to GCS", progress=40)
        artifacts, chunks_payload = self.pipeline.load_local_build(version_id)
        path = pdf_path or self.pipeline.resolve_build_pdf(version_id)
        published = self.pipeline.publish(artifacts, chunks_payload, pdf_path=path)
        self.job_repo.update_job(
            job_id,
            version_id=published.version_id,
            progress=90,
            current_step="Published",
        )

    def _run_index_update(
        self, job_id: str, version_id: str | None, complete_overwrite: bool
    ) -> None:
        if not version_id:
            raise ValueError("version_id is required for index_update")
        self.job_repo.update_job(
            job_id, current_step="Updating Vertex index", progress=50
        )
        delta_uri = self.pipeline.paths.embeddings_delta_uri(version_id)
        count = VertexIndexManager(settings).update_index(
            delta_uri, complete_overwrite=complete_overwrite
        )
        self.job_repo.update_job(
            job_id,
            progress=95,
            current_step=f"Index updated ({count} vectors)",
        )

    def _run_index_deploy(self, deploy_force: bool) -> None:
        self.pipeline.deploy(force=deploy_force)

    def _run_index_create(self, version_id: str | None) -> None:
        if not version_id:
            raise ValueError("version_id is required for index_create")
        delta_uri = self.pipeline.paths.embeddings_delta_uri(version_id)
        VertexIndexManager(settings).create_index(initial_embeddings_uri=delta_uri)

    def _run_rebuild(
        self,
        job_id: str,
        pdf_path: Path | None,
        options: CatalogJobOptions,
    ) -> None:
        path = self.require_uploaded_pdf(pdf_path)
        self.job_repo.update_job(
            job_id, current_step="Chunking and embedding", progress=15
        )
        result = self.pipeline.run(
            path,
            skip_publish=options.skip_publish,
            skip_index_update=options.skip_index_update,
            deploy_after=options.deploy_after,
            deploy_force=options.deploy_force,
            complete_overwrite=options.complete_overwrite,
        )
        self.job_repo.update_job(
            job_id,
            version_id=result.version_id,
            progress=90,
            current_step="Rebuild finished",
            metadata_update={
                "vector_count": result.vector_count,
                "index_vector_count": result.index_vector_count,
            },
        )
