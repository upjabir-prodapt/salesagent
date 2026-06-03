from unittest.mock import MagicMock, patch

import src.dependencies.service_dependencies as deps


def test_get_bigquery_client_singleton():
    deps._bq_client = None
    mock_client = MagicMock()
    with patch(
        "src.dependencies.service_dependencies.bigquery.Client",
        return_value=mock_client,
    ):
        first = deps.get_bigquery_client()
        second = deps.get_bigquery_client()
    assert first is second
    assert first is mock_client
    deps._bq_client = None


def test_get_storage_client_singleton():
    deps._storage_client = None
    mock_client = MagicMock()
    with patch(
        "src.dependencies.service_dependencies.storage.Client",
        return_value=mock_client,
    ):
        first = deps.get_storage_client()
        second = deps.get_storage_client()
    assert first is second
    deps._storage_client = None


def test_get_genai_client_singleton():
    deps._genai_client = None
    mock_client = MagicMock()
    with patch(
        "src.dependencies.service_dependencies.genai.Client",
        return_value=mock_client,
    ):
        first = deps.get_genai_client()
        second = deps.get_genai_client()
    assert first is second
    deps._genai_client = None


def test_repository_and_service_factories():
    with (
        patch.object(deps, "get_bigquery_client", return_value=MagicMock()),
        patch.object(deps, "get_storage_client", return_value=MagicMock()),
        patch(
            "src.repositories.bigquery_repository.BigQueryRepository"
        ) as mock_bq_repo,
        patch("src.repositories.gcs_repository.GCSRepository") as mock_gcs_repo,
        patch("src.services.research.ResearchService") as mock_research,
        patch("src.services.catalog.CatalogService") as mock_catalog,
    ):
        deps.get_bigquery_repository()
        deps.get_gcs_repository()
        deps.get_research_service()
        deps.get_catalog_service()
    assert mock_bq_repo.call_count >= 1
    assert mock_gcs_repo.call_count >= 1
    mock_research.assert_called_once()
    mock_catalog.assert_called_once()
