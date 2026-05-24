import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.routes.app import app

# Suppress experimental and user warnings during tests
warnings.filterwarnings("ignore", category=UserWarning)


@pytest.fixture
def mock_settings():
    with patch("src.core.config.settings") as mock:
        mock.GOOGLE_CLOUD_PROJECT = "test-project"
        mock.APP_NAME = "Sales Research API"
        mock.APP_VERSION = "1.0.0"
        mock.DEBUG = True
        mock.LOG_LEVEL = "INFO"
        mock.CORS_ALLOW_ORIGINS = ["http://localhost:3000"]
        mock.CORS_ALLOW_CREDENTIALS = True
        mock.CORS_ALLOW_METHODS = ["*"]
        mock.CORS_ALLOW_HEADERS = ["*"]
        mock.BIGQUERY_DATASET = "test_dataset"
        mock.BIGQUERY_TABLE = "test_table"
        mock.BIGQUERY_MODEL_CARD_TABLE = "test_model_card_table"
        mock.BIGQUERY_AGENT_TELEMETRY_TABLE = "test_telemetry_table"
        mock.BIGQUERY_COST_ATTRIBUTION_TABLE = "test_cost_attribution_table"
        mock.GCS_BUCKET_NAME = "test-bucket"
        mock.GCS_PARENT_FOLDER = "research"
        mock.GOOGLE_CLOUD_LOCATION = "US"
        mock.GEMINI_MODEL = "gemini-1.5-flash"
        mock.PROMPT_TEMPLATE_VERSION = "v1"
        mock.GEMINI_COST_PER_1K_INPUT_TOKENS = 0.000125
        mock.GEMINI_COST_PER_1K_OUTPUT_TOKENS = 0.000375
        mock.OUTPUT_GUARDRAIL_MAX_RETRIES = 1
        mock.AGENT_RETRY_ATTEMPTS = 1
        mock.AGENT_RETRY_WAIT_FIXED = 1
        mock.RESEARCH_INIT_PROGRESS = 5
        mock.RESEARCH_INIT_STEP_LABEL = "Initializing"
        mock.RESEARCH_UPLOAD_PROGRESS = 90
        mock.RESEARCH_UPLOAD_STEP_LABEL = "Uploading"
        mock.RESEARCH_EVAL_PROGRESS = 95
        mock.RESEARCH_EVAL_STEP_LABEL = "Evaluating"
        mock.OTEL_ENABLED = False
        mock.JOB_ID_PREFIX = "job_"
        mock.API_PREFIX = "/api/v1"
        mock.agent_progress_map = {
            "ResearchOrchestrator": (50, "Researching"),
            "ResearchValidator": (70, "Validating"),
            "AlignmentAnalyst": (80, "Analyzing"),
            "ReportCompiler": (90, "Compiling"),
        }
        yield mock


@pytest.fixture
def mock_bq_client():
    client = MagicMock()
    return client


@pytest.fixture
def mock_storage_client():
    client = MagicMock()
    return client


@pytest.fixture(autouse=True)
def mock_client_pool(mock_bq_client, mock_storage_client):
    with (
        patch("src.dependencies.service_dependencies.get_bigquery_client", return_value=mock_bq_client),
        patch("src.dependencies.service_dependencies.get_storage_client", return_value=mock_storage_client),
        patch("src.repositories.bigquery_repository.get_bigquery_client", return_value=mock_bq_client),
        patch("src.repositories.gcs_repository.get_storage_client", return_value=mock_storage_client),
    ):
        yield


@pytest.fixture(autouse=True)
def patch_repository_settings(mock_settings):
    """Ensure repositories use mock settings."""
    with (
        patch("src.repositories.bigquery_repository.settings", mock_settings),
        patch("src.repositories.gcs_repository.settings", mock_settings),
        patch("src.services.research.research_service.settings", mock_settings),
        patch("src.routes.app.settings", mock_settings),
        patch("src.core.logging_config.settings", mock_settings),
        patch("src.core.security.settings", mock_settings),
    ):
        yield


@pytest.fixture
def client():
    """FastAPI test client with mocked research service."""
    from src.dependencies.auth import get_current_user
    from src.dependencies.handler_dependencies import get_research_handler
    from src.handlers.research_handler import ResearchHandler

    mock_service = MagicMock()
    mock_service.process_research_background = AsyncMock()
    mock_service.new_job_id.return_value = "test-job-123"
    mock_service.create_research_request.return_value = True

    app.dependency_overrides[get_research_handler] = lambda: ResearchHandler(
        mock_service
    )
    app.dependency_overrides[get_current_user] = lambda: {
        "email": "test@example.com",
        "business_unit": "Sales",
        "organization": "Acme",
    }

    # Mock startup initialization to avoid real cloud calls
    with (
        patch("src.routes.app._init_bigquery", new_callable=AsyncMock),
        patch("src.routes.app._init_gcs", new_callable=AsyncMock),
        patch("src.routes.app._init_telemetry"),
        TestClient(app) as c,
    ):
        c.mock_service = mock_service
        yield c

    app.dependency_overrides.clear()
