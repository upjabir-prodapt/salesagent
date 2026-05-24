"""Tests for vector catalog pipeline helpers."""

from pathlib import Path

from src.core.config import settings
from src.services.catalog.chunking import chunk_pdf, split_text
from src.services.catalog.paths import CatalogPaths


def test_split_text_produces_chunks():
    text = "word " * 500
    chunks = split_text(text, chunk_size=100, overlap=10)
    assert len(chunks) >= 2
    assert all(len(c) <= 100 for c in chunks)


def test_catalog_paths_layout():
    paths = CatalogPaths(settings)
    version = "abc12345"
    assert paths.release_vectors_blob(version).endswith("embeddings/data.json")
    assert paths.current_chunks_blob() == settings.vector_search_catalog_chunks_blob
    manifest = paths.build_manifest(
        active_version=version,
        vector_count=10,
        source_sha256="sha",
    )
    assert manifest["active_version"] == version
    assert manifest["releases"][version]["vector_count"] == 10


def test_chunk_pdf_uses_settings_prefix(tmp_path, monkeypatch):
    pdf = tmp_path / "catalog.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    class FakeChunk:
        chunk_id = f"{settings.VECTOR_SEARCH_CHUNK_ID_PREFIX}0"
        text = "sample"

    monkeypatch.setattr(
        "src.services.catalog.chunking.extract_pdf_text",
        lambda _path: "sample text for chunking",
    )
    monkeypatch.setattr(
        "src.services.catalog.chunking.split_text",
        lambda *_args, **_kwargs: ["sample text for chunking"],
    )

    result = chunk_pdf(settings, pdf)
    assert result.chunks[0].chunk_id.startswith(settings.VECTOR_SEARCH_CHUNK_ID_PREFIX)
