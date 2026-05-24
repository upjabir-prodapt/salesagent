"""GCS publish and local artifact writers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from google.cloud import storage

from ...core.config import Settings
from .embeddings import VectorRecord
from .paths import CatalogPaths


@dataclass(frozen=True)
class LocalArtifacts:
    version_id: str
    source_sha256: str
    output_dir: Path
    vectors_path: Path
    chunks_path: Path
    vector_count: int


@dataclass(frozen=True)
class PublishedRelease:
    version_id: str
    manifest_uri: str
    embeddings_delta_uri: str
    current_chunks_uri: str


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_local_artifacts(
    settings: Settings,
    *,
    version_id: str,
    source_sha256: str,
    pdf_path: Path,
    vector_records: list[VectorRecord],
) -> LocalArtifacts:
    output_dir = settings.VECTOR_SEARCH_LOCAL_BUILD_DIR / version_id
    output_dir.mkdir(parents=True, exist_ok=True)

    vectors_path = output_dir / "data.json"
    write_jsonl(
        vectors_path,
        [{"id": r.chunk_id, "embedding": r.embedding} for r in vector_records],
    )

    chunks_payload = {
        "catalog_id": settings.VECTOR_SEARCH_CATALOG_ROOT,
        "version_id": version_id,
        "source_sha256": source_sha256,
        "generated_at": datetime.now(UTC).isoformat(),
        "chunks": [{"id": r.chunk_id, "text": r.text} for r in vector_records],
    }
    chunks_path = output_dir / "chunks.json"
    chunks_path.write_text(
        json.dumps(chunks_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    source_copy = output_dir / pdf_path.name
    source_copy.write_bytes(pdf_path.read_bytes())

    return LocalArtifacts(
        version_id=version_id,
        source_sha256=source_sha256,
        output_dir=output_dir,
        vectors_path=vectors_path,
        chunks_path=chunks_path,
        vector_count=len(vector_records),
    )


class GcsPublisher:
    def __init__(
        self,
        settings: Settings,
        paths: CatalogPaths,
        client: storage.Client | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._client = client or storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        self._bucket = self._client.bucket(settings.VECTOR_SEARCH_BUCKET)

    def _upload_file(self, local: Path, blob_name: str, content_type: str) -> str:
        blob = self._bucket.blob(blob_name)
        blob.upload_from_filename(str(local), content_type=content_type)
        return self._paths.gcs_uri(blob_name)

    def _upload_json(self, payload: dict, blob_name: str) -> str:
        blob = self._bucket.blob(blob_name)
        blob.upload_from_string(
            json.dumps(payload, ensure_ascii=False, indent=2),
            content_type="application/json",
        )
        return self._paths.gcs_uri(blob_name)

    def _copy_blob(self, source_blob: str, dest_blob: str) -> None:
        src = self._bucket.blob(source_blob)
        self._bucket.copy_blob(src, self._bucket, dest_blob)

    def publish(
        self,
        artifacts: LocalArtifacts,
        *,
        pdf_path: Path,
        chunks_payload: dict,
    ) -> PublishedRelease:
        version_id = artifacts.version_id
        paths = self._paths

        self._upload_json(chunks_payload, paths.release_chunks_blob(version_id))
        self._copy_blob(
            paths.release_chunks_blob(version_id),
            paths.current_chunks_blob(),
        )
        embeddings_prefix = f"{paths.release_embeddings_dir(version_id)}/"
        for blob in self._bucket.list_blobs(prefix=embeddings_prefix):
            blob.delete()

        self._upload_file(
            artifacts.vectors_path,
            paths.release_vectors_blob(version_id),
            content_type="application/json",
        )
        self._upload_file(
            pdf_path,
            paths.release_source_blob(version_id),
            content_type="application/pdf",
        )
        manifest = paths.build_manifest(
            active_version=version_id,
            vector_count=artifacts.vector_count,
            source_sha256=artifacts.source_sha256,
        )
        manifest_uri = self._upload_json(manifest, paths.manifest_blob())

        return PublishedRelease(
            version_id=version_id,
            manifest_uri=manifest_uri,
            embeddings_delta_uri=paths.embeddings_delta_uri(version_id),
            current_chunks_uri=paths.gcs_uri(paths.current_chunks_blob()),
        )
