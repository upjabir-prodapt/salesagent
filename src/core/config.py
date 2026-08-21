"""Configuration: all values come from environment / .env (see .env.example)."""

import json
import os
from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_repo_path(path: str | Path) -> Path:
    """Resolve a path relative to the repository root when not absolute."""
    p = Path(path)
    if p.is_absolute():
        return p
    return (_REPO_ROOT / p).resolve()


# Must match Cloud Run deploy: --set-secrets="/secrets/.env=..." (azure-pipelines.yml)
LOCAL_ENV_FILE = _REPO_ROOT / ".env"
CLOUD_RUN_ENV_FILE = Path("/secrets/.env")


def _env_flag(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip().lower() in ("1", "true", "yes")


def is_local_runtime() -> bool:
    explicit = _env_flag("IS_LOCAL")
    if explicit is not None:
        return explicit
    return True


def resolve_dotenv_path() -> Path | None:
    if _env_flag("DOTENV_DISABLE"):
        return None
    raw = os.getenv("DOTENV_PATH", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (_REPO_ROOT / path).resolve()
        return path
    return LOCAL_ENV_FILE if is_local_runtime() else CLOUD_RUN_ENV_FILE


def load_dotenv_file(path: Path | None = None) -> Path | None:
    target = resolve_dotenv_path() if path is None else path
    if target is None:
        return None
    if target.is_file():
        load_dotenv(target)
        return target
    return None


_DOTENV_FILE = load_dotenv_file()


class Settings(BaseSettings):
    """Settings loaded from env vars and the resolved dotenv file."""

    # Application
    APP_NAME: str
    APP_VERSION: str
    API_PREFIX: str
    DEBUG: bool
    IS_LOCAL: bool
    HOST: str
    PORT: int
    WORKERS: int
    LOG_LEVEL: str
    LOG_FILE: str | None = None
    AGENT_EVENT_LOG_VERBOSE: bool
    AGENT_EVENT_LOG_FILE: str | None = None

    # Google Cloud
    GOOGLE_CLOUD_PROJECT: str = Field(
        validation_alias=AliasChoices("GOOGLE_CLOUD_PROJECT", "GOOGLE_PROJECT_ID"),
    )
    GOOGLE_CLOUD_LOCATION: str = Field(
        validation_alias=AliasChoices(
            "GOOGLE_CLOUD_LOCATION", "GOOGLE_PROJECT_LOCATION"
        ),
    )
    GOOGLE_GENAI_USE_VERTEXAI: bool
    GOOGLE_CLOUD_QUOTA_PROJECT: str

    # BigQuery
    BIGQUERY_DATASET: str
    BIGQUERY_TABLE: str
    BIGQUERY_COST_ATTRIBUTION_TABLE: str
    BIGQUERY_AGENT_TELEMETRY_TABLE: str
    BIGQUERY_CATALOG_JOBS_TABLE: str
    BIGQUERY_USER_FEEDBACK_TABLE: str

    # Security
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    IAP_AUDIENCE: str = ""
    HUB_IAP_AUDIENCE: str = ""
    PROMPT_TEMPLATE_VERSION: str

    # GCS
    GCS_BUCKET_NAME: str
    GCS_SIGNED_URL_EXPIRATION_HOURS: int
    GCS_PARENT_FOLDER: str

    # CORS
    CORS_ALLOW_ORIGINS: list[str]
    CORS_ALLOW_CREDENTIALS: bool
    CORS_ALLOW_METHODS: list[str]
    CORS_ALLOW_HEADERS: list[str]

    # OpenTelemetry
    OTEL_ENABLED: bool
    OTEL_SERVICE_NAME: str
    OTEL_EXPORTER_OTLP_ENDPOINT: str
    OTEL_EXPORTER_OTLP_PROTOCOL: str
    OTEL_RESOURCE_ATTRIBUTES: str
    OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: bool
    OTEL_SEMCONV_STABILITY_OPT_IN: str
    OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: str
    ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS: bool

    # Research / Gemini
    GEMINI_MODEL: str
    SEARCH_AGENT_MODEL: str = "gemini-2.5-flash"
    RESEARCH_STATUS_MIN_UPDATE_INTERVAL_SECONDS: float
    GEMINI_RETRY_ATTEMPTS: int
    GEMINI_RETRY_INITIAL_DELAY: int
    GEMINI_RETRY_MAX_DELAY: int
    GEMINI_RETRY_EXP_BASE: int
    GEMINI_RETRY_JITTER: int
    GEMINI_RETRY_STATUS_CODES: list[int]
    GEMINI_MODEL_PRICING_JSON: str
    EVALUATOR_MODEL: str
    AGENT_EVENTS_COMPACT_ENABLED: bool = True
    AGENT_EVENTS_COMPACT_TOKEN_THRESHOLD: int = 100_000
    AGENT_EVENTS_COMPACT_RETENTION: int = 6
    AGENT_EVENTS_COMPACT_INTERVAL: int = 3
    AGENT_EVENTS_COMPACT_OVERLAP: int = 1
    AGENT_COMPACT_SUMMARIZER_MODEL: str = "gemini-2.5-flash"
    EVAL_EMBEDDING_ENABLED: bool = True
    EVAL_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EVAL_EMBEDDING_ONNX_PATH: str = "models/all-MiniLM-L6-v2/onnx/model.onnx"
    EVAL_EMBEDDING_SIMILARITY_THRESHOLD: float = 0.55
    AGENT_RETRY_ATTEMPTS: int
    AGENT_RETRY_WAIT_FIXED: int
    JOB_ID_PREFIX: str
    CATALOG_JOB_ID_PREFIX: str
    RESEARCH_INIT_STEP_LABEL: str
    RESEARCH_INIT_PROGRESS: int
    RESEARCH_UPLOAD_STEP_LABEL: str
    RESEARCH_UPLOAD_PROGRESS: int
    RESEARCH_EVAL_STEP_LABEL: str
    RESEARCH_EVAL_PROGRESS: int

    # Safety
    SAFETY_HARASSMENT_THRESHOLD: str
    SAFETY_HATE_SPEECH_THRESHOLD: str
    SAFETY_SEXUAL_THRESHOLD: str
    SAFETY_DANGEROUS_THRESHOLD: str
    SAFETY_LOGGING_ENABLED: bool

    # Output guardrails
    OUTPUT_GUARDRAIL_HALLUCINATION_MODEL: str
    OUTPUT_GUARDRAIL_MIN_SECTIONS: int
    OUTPUT_GUARDRAIL_MAX_RETRIES: int
    OUTPUT_GUARDRAIL_HALLUCINATION_BLOCK_THRESHOLD: int

    # Vector search
    VECTOR_SEARCH_INDEX_ID: str
    VECTOR_SEARCH_INDEX_ENDPOINT_ID: str
    VECTOR_SEARCH_DEPLOYED_INDEX_ID: str
    VECTOR_SEARCH_PSC_IP: str | None = None
    VECTOR_SEARCH_EMBEDDING_MODEL: str
    VECTOR_SEARCH_NUM_NEIGHBORS: int
    VECTOR_SEARCH_BUCKET: str
    VECTOR_SEARCH_CATALOG_ROOT: str
    VECTOR_SEARCH_CATALOG_CHUNKS_BLOB: str | None = None
    VECTOR_SEARCH_CHUNK_SIZE: int
    VECTOR_SEARCH_CHUNK_OVERLAP: int
    VECTOR_SEARCH_CHUNK_ID_PREFIX: str
    VECTOR_SEARCH_EMBEDDING_DIMENSIONS: int
    VECTOR_SEARCH_EMBEDDING_BATCH_SIZE: int
    VECTOR_SEARCH_LOCAL_BUILD_DIR: Path
    VECTOR_SEARCH_INDEX_DISPLAY_NAME: str
    VECTOR_SEARCH_ENDPOINT_DISPLAY_NAME: str
    VECTOR_SEARCH_DISTANCE_MEASURE_TYPE: str
    VECTOR_SEARCH_APPROXIMATE_NEIGHBORS_COUNT: int
    VECTOR_SEARCH_LEAF_NODE_EMBEDDING_COUNT: int
    VECTOR_SEARCH_LEAF_NODES_TO_SEARCH_PERCENT: float
    VECTOR_SEARCH_DEPLOY_MACHINE_TYPE: str
    VECTOR_SEARCH_DEPLOY_MIN_REPLICAS: int
    VECTOR_SEARCH_DEPLOY_MAX_REPLICAS: int
    VECTOR_SEARCH_INDEX_UPDATE_POLL_INTERVAL_SEC: int
    VECTOR_SEARCH_INDEX_UPDATE_TIMEOUT_SEC: int

    # Search API pricing (USD per 1000 requests)
    GOOGLE_SEARCH_PRICING_3X: str | None = "14.0"
    GOOGLE_SEARCH_PRICING_2X: str | None = "35.0"

    model_config = SettingsConfigDict(
        env_file=_DOTENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator("GEMINI_MODEL_PRICING_JSON", mode="before")
    @classmethod
    def _validate_gemini_model_pricing_json(cls, value: object) -> str:
        if value is None or (isinstance(value, str) and not str(value).strip()):
            raise ValueError(
                "GEMINI_MODEL_PRICING_JSON is required and must not be empty"
            )
        if isinstance(value, str):
            data = json.loads(value)
        elif isinstance(value, dict):
            data = value
        else:
            raise ValueError("GEMINI_MODEL_PRICING_JSON must be a JSON object")

        if not data:
            raise ValueError(
                "GEMINI_MODEL_PRICING_JSON must contain at least one model"
            )

        for model, rates in data.items():
            if not isinstance(rates, dict):
                raise ValueError(
                    f"GEMINI_MODEL_PRICING_JSON entry for {model!r} must be an object"
                )
            for field in ("input_per_1m", "output_per_1m"):
                if field not in rates:
                    raise ValueError(
                        f"GEMINI_MODEL_PRICING_JSON entry for {model!r} "
                        f"missing required field {field!r}"
                    )
                float(rates[field])

        return json.dumps(data) if isinstance(value, dict) else str(value)

    @field_validator(
        "LOG_FILE",
        "AGENT_EVENT_LOG_FILE",
        "VECTOR_SEARCH_CATALOG_CHUNKS_BLOB",
        "VECTOR_SEARCH_PSC_IP",
        mode="before",
    )
    @classmethod
    def _empty_optional_str(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _sync_sdk_environment(self) -> Self:
        """Push GenAI / OTEL SDK settings into os.environ (read by ADK and auto-instrumentation)."""
        if not self.IS_LOCAL and not self.IAP_AUDIENCE:
            raise ValueError("IAP_AUDIENCE is required when IS_LOCAL is false")
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = (
            "true" if self.GOOGLE_GENAI_USE_VERTEXAI else "false"
        )
        os.environ["GOOGLE_CLOUD_QUOTA_PROJECT"] = self.GOOGLE_CLOUD_QUOTA_PROJECT
        os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = self.OTEL_EXPORTER_OTLP_ENDPOINT
        os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = self.OTEL_EXPORTER_OTLP_PROTOCOL
        os.environ["OTEL_RESOURCE_ATTRIBUTES"] = self.OTEL_RESOURCE_ATTRIBUTES
        os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] = self.OTEL_SEMCONV_STABILITY_OPT_IN
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = (
            self.OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT
        )
        os.environ["ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"] = (
            "true" if self.ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS else "false"
        )
        os.environ["OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED"] = (
            "true" if self.OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED else "false"
        )
        return self

    @field_validator("VECTOR_SEARCH_LOCAL_BUILD_DIR", mode="before")
    @classmethod
    def _resolve_vector_build_dir(cls, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (_REPO_ROOT / path).resolve()
        return path

    @property
    def eval_embedding_onnx_path(self) -> Path:
        return resolve_repo_path(self.EVAL_EMBEDDING_ONNX_PATH)

    @property
    def app_log_path(self) -> Path | None:
        """Resolved path for mirrored application logs, or None when disabled."""
        if not self.LOG_FILE:
            return None
        return resolve_repo_path(self.LOG_FILE)

    @property
    def agent_event_log_path(self) -> Path | None:
        """Resolved path for ADK event file logging, or None when disabled."""
        if not self.AGENT_EVENT_LOG_FILE:
            return None
        return resolve_repo_path(self.AGENT_EVENT_LOG_FILE)

    @property
    def commit_sha(self) -> str | None:
        """Deployment revision from process env (CI / Cloud Run), not from .env."""
        raw = os.environ.get("COMMIT_SHA", "").strip()
        return raw or None

    @property
    def vector_search_catalog_chunks_blob(self) -> str:
        if self.VECTOR_SEARCH_CATALOG_CHUNKS_BLOB:
            return self.VECTOR_SEARCH_CATALOG_CHUNKS_BLOB
        return f"{self.VECTOR_SEARCH_CATALOG_ROOT}/current/chunks.json"

    @property
    def bigquery_table_ref(self) -> str:
        return (
            f"{self.GOOGLE_CLOUD_PROJECT}.{self.BIGQUERY_DATASET}.{self.BIGQUERY_TABLE}"
        )

    @property
    def gcs_bucket_uri(self) -> str:
        return f"gs://{self.GCS_BUCKET_NAME}"

    @property
    def bigquery_catalog_jobs_table_ref(self) -> str:
        return (
            f"{self.GOOGLE_CLOUD_PROJECT}."
            f"{self.BIGQUERY_DATASET}."
            f"{self.BIGQUERY_CATALOG_JOBS_TABLE}"
        )


settings = Settings()
