from types import SimpleNamespace

from src.services.catalog.paths import SOURCE_FILENAME, CatalogPaths


def test_catalog_paths_and_manifest():
    settings = SimpleNamespace(
        VECTOR_SEARCH_CATALOG_ROOT="colt-product-catalog",
        VECTOR_SEARCH_BUCKET="test-vector-bucket",
        vector_search_catalog_chunks_blob="colt-product-catalog/current/chunks.json",
        VECTOR_SEARCH_EMBEDDING_MODEL="text-embedding-004",
        VECTOR_SEARCH_CHUNK_SIZE=900,
        VECTOR_SEARCH_CHUNK_OVERLAP=120,
        VECTOR_SEARCH_INDEX_ID="idx-1",
        VECTOR_SEARCH_INDEX_ENDPOINT_ID="ep-1",
        VECTOR_SEARCH_DEPLOYED_INDEX_ID="deployed-1",
    )
    paths = CatalogPaths(settings)

    version = "2026-06-03"
    assert paths.release_source_blob(version).endswith(SOURCE_FILENAME)
    assert paths.gcs_uri("a/b") == "gs://test-vector-bucket/a/b"
    assert paths.embeddings_delta_uri(version).startswith(
        "gs://test-vector-bucket/colt-product-catalog/releases/"
    )

    manifest = paths.build_manifest(
        active_version=version,
        vector_count=42,
        source_sha256="abc123",
    )
    assert manifest["active_version"] == version
    assert manifest["releases"][version]["vector_count"] == 42
