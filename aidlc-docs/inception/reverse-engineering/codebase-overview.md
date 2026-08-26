# Codebase Overview & Reverse Engineering Scan

## Workspace Context
- **Type**: Brownfield
- **Primary Language & Version**: Python 3.11 (`.python-version: 3.11`)
- **Package Manager**: `uv` (Astral), `pyproject.toml`, `uv.lock`
- **Frameworks**: FastAPI 0.123+, Google ADK 2.1.0, Google GenAI SDK (`google-genai`), Pydantic v2
- **Infrastructure**: Google Cloud Tasks, Google Cloud Storage, Google BigQuery, Google Firestore, Redis / Cloud Memorystore, Google Cloud Trace (OpenTelemetry)

## Package Layout & Responsibilities

### 1. `src/api/` (Public API Role, `APP_ROLE=api`)
- `main.py`: ASGI application lifecycle, CORS, routes.
- `dependencies.py`: Dependency injection providers for handlers, security, and services.
- `routes/auth.py`, `routes/research.py`: Public HTTP REST endpoints.
- `handlers/research_handler.py`: Request validation, BigQuery job record creation (`PENDING`), Cloud Tasks enqueue.
- `services/cloud_tasks_service.py`: Enqueues HTTP POST requests targeting worker service with Google OIDC authentication.
- `services/research_job_service.py`: Status querying and report artifact retrieval.

### 2. `src/worker/` (Internal Background Worker Role, `APP_ROLE=worker`)
- `main.py`: Internal ASGI application mounting `/health` and `POST /internal/tasks/research`.
- `core/cloud_tasks_auth.py`: OIDC bearer token verification (`require_cloud_tasks_oidc`).
- `handlers/research_task_handler.py`: Task consumer with W3C trace context extraction and job idempotency gating.
- `services/research_pipeline_service.py`: Coordinates runner, artifact storage, and finalization.
- `agents/`: ADK agent graph, factories, prompts, tools, callbacks, and composition.
- `pipeline/`: `ResearchJobOrchestrator`, adapters (`BigQueryStatusAdapter`, `AdkRunnerAdapter`, `GcsArtifactAdapter`, `FinalizationAdapter`), commands.
- `run/`: Runner lifecycle (`runner.py`), resilience loop (`runner_loop.py`), telemetry (`telemetry.py`), progress tracking (`progress.py`).
- `finalization/`: PDF rendering (WeasyPrint), `EvaluationService` (Section A LLM judge + Section B automated rubric), BigQuery telemetry flush.

### 3. `src/shared/` (Shared Infrastructure)
- `config.py`: Unified Pydantic `Settings`.
- `logging_config.py`: Structured contextual logging.
- `otel_setup.py`: OpenTelemetry TracerProvider targeting Cloud Trace via OTLP/HTTP.
- `repositories/`: BigQuery (`BigQueryRepository`), GCS (`GCSRepository`), Firestore (`FirestoreSearchCacheRepository`), Redis (`RedisSearchCacheRepository`), Clients singleton pool (`clients.py`).
- `utils/`: Guardrails (`guardrails.py`), OpenTelemetry decorators (`tracing.py`), grounding, URL parsing.
