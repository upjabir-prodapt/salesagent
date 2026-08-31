from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from tests._bootstrap import SESSION_MP  # isort: skip

import src.shared.config as core_config
import src.shared.logging_config as logging_config
import src.shared.repositories.bigquery_repository as bigquery_repository
import src.shared.repositories.gcs_repository as gcs_repository
import src.shared.utils.guardrails as guardrails
from src.api.dependencies import get_current_user, get_research_handler
from src.api.handlers.research_handler import ResearchHandler
from src.api.main import app

# Seed the minimal mounted assets (pricing_catalog.json, ColtProductCatalog.pdf)
# that Settings.validate_mounted_assets() requires at FastAPI/Worker startup.
# Production reads these from a GCS-mounted volume (Cloud Run --add-volume);
# local dev keeps a copy on disk under assets/ or data/ for zero-infra testing,
# but neither is tracked in git (see .gitignore). A fresh CI checkout therefore
# has none of the paths `assets_root_path` searches, so every test that spins
# up TestClient(app) (triggering the startup lifespan) would otherwise fail
# with "Required mounted assets missing at startup". Seeding here mirrors the
# same fixture pattern used by the Translation service's tests/conftest.py.
_assets_dir = core_config.settings.assets_root_path
_assets_dir.mkdir(parents=True, exist_ok=True)

_test_pricing_catalog_path = _assets_dir / core_config.settings.PRICING_CATALOG_FILENAME
if not _test_pricing_catalog_path.is_file():
    _test_pricing_catalog_path.write_text(
        json.dumps(
            [
                {
                    "provider": "gemini_vertexai",
                    "model_id": "gemini-3.5-flash",
                    "region": "europe-west3",
                    "context_window_tokens": 1048576,
                    "search_cost_per_1k": 35.0,
                    "tiers": [
                        {
                            "max_input_tokens": None,
                            "input_cost_per_1k": 0.00165,
                            "output_cost_per_1k": 0.0099,
                            "cache_hit_cost_per_1k": 0.000165,
                        }
                    ],
                },
                {
                    "provider": "gemini_vertexai",
                    "model_id": "gemini-3.5-flash",
                    "region": None,
                    "context_window_tokens": 1048576,
                    "search_cost_per_1k": 35.0,
                    "tiers": [
                        {
                            "max_input_tokens": None,
                            "input_cost_per_1k": 0.0015,
                            "output_cost_per_1k": 0.009,
                            "cache_hit_cost_per_1k": 0.00015,
                        }
                    ],
                },
                {
                    "provider": "gemini_vertexai",
                    "model_id": "gemini-2.5-pro",
                    "region": "europe-west1",
                    "context_window_tokens": 2097152,
                    "search_cost_per_1k": 35.0,
                    "tiers": [
                        {
                            "max_input_tokens": 200000,
                            "input_cost_per_1k": 0.00125,
                            "output_cost_per_1k": 0.01,
                            "cache_hit_cost_per_1k": 0.00013,
                        },
                        {
                            "max_input_tokens": None,
                            "input_cost_per_1k": 0.0025,
                            "output_cost_per_1k": 0.015,
                            "cache_hit_cost_per_1k": 0.00025,
                        },
                    ],
                },
                {
                    "provider": "gemini_vertexai",
                    "model_id": "gemini-2.5-pro",
                    "region": None,
                    "context_window_tokens": 2097152,
                    "search_cost_per_1k": 35.0,
                    "tiers": [
                        {
                            "max_input_tokens": 200000,
                            "input_cost_per_1k": 0.00125,
                            "output_cost_per_1k": 0.01,
                            "cache_hit_cost_per_1k": 0.00013,
                        },
                        {
                            "max_input_tokens": None,
                            "input_cost_per_1k": 0.0025,
                            "output_cost_per_1k": 0.015,
                            "cache_hit_cost_per_1k": 0.00025,
                        },
                    ],
                },
                {
                    "provider": "gemini_vertexai",
                    "model_id": "gemini-2.5-flash",
                    "region": None,
                    "context_window_tokens": 1048576,
                    "search_cost_per_1k": 35.0,
                    "tiers": [
                        {
                            "max_input_tokens": None,
                            "input_cost_per_1k": 0.0003,
                            "output_cost_per_1k": 0.0025,
                            "cache_hit_cost_per_1k": 0.00003,
                        }
                    ],
                },
                {
                    "provider": "gemini_vertexai",
                    "model_id": "gemini-2.5-flash-lite",
                    "region": None,
                    "context_window_tokens": 1048576,
                    "search_cost_per_1k": 35.0,
                    "tiers": [
                        {
                            "max_input_tokens": None,
                            "input_cost_per_1k": 0.0001,
                            "output_cost_per_1k": 0.0004,
                            "cache_hit_cost_per_1k": 0.00001,
                        }
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )

_test_colt_catalog_path = _assets_dir / core_config.settings.COLT_CATALOG_FILENAME
if not _test_colt_catalog_path.is_file():
    import pypdf

    _writer = pypdf.PdfWriter()
    _writer.add_blank_page(width=200, height=200)
    with open(_test_colt_catalog_path, "wb") as _fh:
        _writer.write(_fh)


@pytest.fixture(scope="session", autouse=True)
def _restore_test_env_after_session():
    yield
    SESSION_MP.undo()


@pytest.fixture
def mock_settings(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    base = {
        name: getattr(core_config.settings, name)
        for name in dir(core_config.settings)
        if name.isupper()
    }
    base.update(
        {
            "APP_NAME": "Sales Agent API",
            "APP_VERSION": "test",
            "API_PREFIX": "/api/v1",
            "DEBUG": True,
            "LOG_LEVEL": "DEBUG",
            "LOG_FILE": None,
            "GOOGLE_CLOUD_PROJECT": "test-project",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
            "BIGQUERY_DATASET": "test_dataset",
            "BIGQUERY_TABLE": "test_table",
            "BIGQUERY_COST_ATTRIBUTION_TABLE": "test_cost_attribution",
            "BIGQUERY_AGENT_TELEMETRY_TABLE": "test_agent_telemetry",
            "BIGQUERY_USER_FEEDBACK_TABLE": "test_users_feedback",
            "GCS_BUCKET_NAME": "test-bucket",
            "GCS_PARENT_FOLDER": "research",
            "OTEL_ENABLED": False,
            "SAFETY_HARASSMENT_THRESHOLD": "BLOCK_MEDIUM_AND_ABOVE",
            "SAFETY_HATE_SPEECH_THRESHOLD": "BLOCK_MEDIUM_AND_ABOVE",
            "SAFETY_SEXUAL_THRESHOLD": "BLOCK_MEDIUM_AND_ABOVE",
            "SAFETY_DANGEROUS_THRESHOLD": "BLOCK_ONLY_HIGH",
            "SAFETY_LOGGING_ENABLED": True,
            "OUTPUT_GUARDRAIL_MIN_SECTIONS": 5,
            "OUTPUT_GUARDRAIL_HALLUCINATION_MODEL": "gemini-2.0-flash",
            "OUTPUT_GUARDRAIL_HALLUCINATION_BLOCK_THRESHOLD": 1,
        }
    )
    mocked = SimpleNamespace(**base, app_log_path=None)

    monkeypatch.setattr(core_config, "settings", mocked)
    monkeypatch.setattr(logging_config, "settings", mocked)
    monkeypatch.setattr(bigquery_repository, "settings", mocked)
    monkeypatch.setattr(gcs_repository, "settings", mocked)
    monkeypatch.setattr(guardrails, "settings", mocked)

    return mocked


@pytest.fixture
def mock_bq_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_storage_client() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import src.api.main as app_module

    mock_service = MagicMock()
    mock_service.new_job_id.return_value = "job_123"
    mock_service.create_research_request.return_value = True
    mock_service.process_research_background = MagicMock()
    mock_service.get_request_status.return_value = {
        "request_id": "job_123",
        "status": "PROCESSING",
        "progress": 50,
        "current_step": "Researching",
    }
    mock_service.get_request_result.return_value = {
        "request_id": "job_123",
        "status": "COMPLETED",
        "report_content": "# Report",
        "download_url": "http://gcs/report.pdf",
        "model_card": {
            "model_version": "gemini-test",
            "tokens_used": 123,
            "latency_seconds": 1.0,
            "cost_usd": 0.01,
        },
    }
    mock_service.get_pdf_report.return_value = (b"%PDF-1.4 test", "Acme Corp")

    previous_overrides = dict(app.dependency_overrides)
    app.dependency_overrides[get_research_handler] = lambda: ResearchHandler(
        mock_service
    )
    app.dependency_overrides[get_current_user] = lambda: {
        "email": "test@colt.net",
        "business_unit": "Sales",
        "organization": "Colt",
    }

    monkeypatch.setattr(app_module, "_init_bigquery", AsyncMock(return_value=None))
    monkeypatch.setattr(app_module, "_init_gcs", AsyncMock(return_value=None))
    monkeypatch.setattr(app_module, "_init_telemetry", lambda _app: None)

    with TestClient(app) as test_client:
        test_client.mock_service = mock_service
        yield test_client

    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous_overrides)
