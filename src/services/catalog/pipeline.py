"""End-to-end catalog vector pipeline orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from google.cloud import storage

from ...core.config import Settings
from ...core.logging_config import logger
from .chunking import chunk_pdf
from .embeddings import embed_chunks
from .paths import CatalogPaths
from .storage import (
    GcsPublisher,
    LocalArtifacts,
    PublishedRelease,
    write_local_artifacts,
)
from .vertex import VertexIndexManager


@dataclass(frozen=True)
class PipelineResult:
    version_id: str
    vector_count: int
    local_dir: Path
    published: PublishedRelease | None
    index_vector_count: int | None


class VectorCatalogPipeline:
    def __init__(
        self,
        settings: Settings,
        storage_client: storage.Client | None = None,
    ) -> None:
        self.settings = settings
        self.paths = CatalogPaths(settings)
        self._storage_client = storage_client

    def _version_from_pdf(self, pdf_path: Path) -> tuple[str, str]:
        pdf_bytes = pdf_path.read_bytes()
        return hashlib.sha256(pdf_bytes).hexdigest()[:8], hashlib.sha256(
            pdf_bytes
        ).hexdigest()

    def prepare(self, pdf_path: Path) -> tuple[LocalArtifacts, dict]:
        pdf = pdf_path.resolve()
        if not pdf.is_file():
            raise FileNotFoundError(f"Catalog PDF not found: {pdf}")

        version_id, source_sha256 = self._version_from_pdf(pdf)
        logger.info("Chunking %s -> version %s", pdf, version_id)
        chunking = chunk_pdf(self.settings, pdf)
        logger.info(
            "%s chars -> %s chunks", len(chunking.full_text), len(chunking.chunks)
        )

        vectors = embed_chunks(self.settings, chunking.chunks)
        artifacts = write_local_artifacts(
            self.settings,
            version_id=version_id,
            source_sha256=source_sha256,
            pdf_path=pdf,
            vector_records=vectors,
        )
        chunks_payload = json.loads(artifacts.chunks_path.read_text(encoding="utf-8"))
        return artifacts, chunks_payload

    def publish(
        self,
        artifacts: LocalArtifacts,
        chunks_payload: dict,
        *,
        pdf_path: Path,
    ) -> PublishedRelease:
        publisher = GcsPublisher(self.settings, self.paths, client=self._storage_client)
        published = publisher.publish(
            artifacts, pdf_path=pdf_path, chunks_payload=chunks_payload
        )
        logger.info("Published manifest %s", published.manifest_uri)
        return published

    def update_index(
        self,
        published: PublishedRelease,
        *,
        complete_overwrite: bool = True,
    ) -> int:
        manager = VertexIndexManager(self.settings)
        return manager.update_index(
            published.embeddings_delta_uri,
            complete_overwrite=complete_overwrite,
        )

    def deploy(self, *, force: bool = False) -> None:
        VertexIndexManager(self.settings).deploy(force=force)

    def resolve_build_pdf(self, version_id: str) -> Path:
        """Return the catalog PDF copied into a local prepare build."""
        build_dir = self.settings.VECTOR_SEARCH_LOCAL_BUILD_DIR / version_id
        if not build_dir.is_dir():
            raise FileNotFoundError(f"No local build at {build_dir}")
        pdfs = sorted(build_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(
                f"No catalog PDF in local build {build_dir}; "
                "upload a PDF when running prepare or rebuild"
            )
        return pdfs[0]

    def load_local_build(self, version_id: str) -> tuple[LocalArtifacts, dict]:
        build_dir = self.settings.VECTOR_SEARCH_LOCAL_BUILD_DIR / version_id
        vectors_path = build_dir / "data.json"
        chunks_path = build_dir / "chunks.json"
        if not vectors_path.is_file() or not chunks_path.is_file():
            raise FileNotFoundError(f"No local build at {build_dir}")

        chunks_payload = json.loads(chunks_path.read_text(encoding="utf-8"))
        chunk_list = chunks_payload.get("chunks", [])
        source_sha256 = chunks_payload.get("source_sha256")
        if not source_sha256:
            _, source_sha256 = self._version_from_pdf(
                self.resolve_build_pdf(version_id)
            )

        artifacts = LocalArtifacts(
            version_id=version_id,
            source_sha256=source_sha256,
            output_dir=build_dir,
            vectors_path=vectors_path,
            chunks_path=chunks_path,
            vector_count=len(chunk_list),
        )
        return artifacts, chunks_payload

    def run(
        self,
        pdf_path: Path,
        *,
        skip_publish: bool = False,
        skip_index_update: bool = False,
        deploy_after: bool = False,
        deploy_force: bool = False,
        complete_overwrite: bool = True,
    ) -> PipelineResult:
        artifacts, chunks_payload = self.prepare(pdf_path)
        published: PublishedRelease | None = None
        index_count: int | None = None

        if not skip_publish:
            published = self.publish(artifacts, chunks_payload, pdf_path=pdf_path)

        if not skip_index_update and published is not None:
            index_count = self.update_index(
                published, complete_overwrite=complete_overwrite
            )
            logger.info("Index ready with %s vectors", index_count)

        if deploy_after:
            self.deploy(force=deploy_force)

        return PipelineResult(
            version_id=artifacts.version_id,
            vector_count=artifacts.vector_count,
            local_dir=artifacts.output_dir,
            published=published,
            index_vector_count=index_count,
        )

    def fetch_manifest(self) -> dict:
        client = self._storage_client or storage.Client(
            project=self.settings.GOOGLE_CLOUD_PROJECT
        )
        blob = client.bucket(self.settings.VECTOR_SEARCH_BUCKET).blob(
            self.paths.manifest_blob()
        )
        return json.loads(blob.download_as_text())
