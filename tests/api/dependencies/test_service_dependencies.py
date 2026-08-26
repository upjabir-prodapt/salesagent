from unittest.mock import MagicMock, patch

import src.api.dependencies as api_deps
import src.shared.repositories.clients as client_deps
import src.worker.dependencies as worker_deps


def test_get_bigquery_client_singleton():
    client_deps._bq_client = None
    mock_client = MagicMock()
    with patch(
        "src.shared.repositories.clients.bigquery.Client",
        return_value=mock_client,
    ):
        first = client_deps.get_bigquery_client()
        second = client_deps.get_bigquery_client()
    assert first is second
    assert first is mock_client
    client_deps._bq_client = None


def test_get_storage_client_singleton():
    client_deps._storage_client = None
    mock_client = MagicMock()
    with patch(
        "src.shared.repositories.clients.storage.Client",
        return_value=mock_client,
    ):
        first = client_deps.get_storage_client()
        second = client_deps.get_storage_client()
    assert first is second
    client_deps._storage_client = None


def test_get_genai_client_singleton():
    client_deps._genai_client = None
    mock_client = MagicMock()
    with patch(
        "src.shared.repositories.clients.genai.Client",
        return_value=mock_client,
    ):
        first = client_deps.get_genai_client()
        second = client_deps.get_genai_client()
    assert first is second
    client_deps._genai_client = None


def test_repository_and_service_factories():
    api_deps._bq_repo = None
    api_deps._gcs_repo = None
    worker_deps._bq_repo = None
    worker_deps._gcs_repo = None
    worker_deps._pipeline_svc = None

    with (
        patch("src.api.dependencies.BigQueryRepository") as mock_bq_repo,
        patch("src.api.dependencies.GCSRepository") as mock_gcs_repo,
        patch("src.api.dependencies.ResearchJobService") as mock_job_svc,
        patch("src.worker.dependencies.ResearchPipelineService") as mock_pipe_svc,
        patch("src.api.dependencies.CloudTasksService") as mock_cloud_tasks,
    ):
        api_deps.get_bigquery_repository()
        api_deps.get_gcs_repository()
        api_deps.get_research_job_service()
        worker_deps.get_research_pipeline_service()
        api_deps.get_cloud_tasks_service()
    assert mock_bq_repo.call_count >= 1
    assert mock_gcs_repo.call_count >= 1
    mock_job_svc.assert_called_once()
    mock_pipe_svc.assert_called_once()
    mock_cloud_tasks.assert_called_once()

    api_deps._bq_repo = None
    api_deps._gcs_repo = None
    worker_deps._bq_repo = None
    worker_deps._gcs_repo = None
    worker_deps._pipeline_svc = None
