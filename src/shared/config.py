"""Configuration: all values come from environment / .env (see .env.example)."""

import os
from pathlib import Path
from typing import Any, Self

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
    APP_ROLE: str = ""  # "api" | "worker" | ""
    DEBUG: bool
    IS_LOCAL: bool
    HOST: str
    PORT: int
    WORKERS: int
    LOG_LEVEL: str
    LOG_FILE: str | None = None
    AGENT_EVENT_LOG_VERBOSE: bool
    AGENT_EVENT_LOG_FILE: str | None = None
    API_USE_BACKGROUND_PIPELINE: bool = True

    # Cloud Tasks
    CLOUD_TASKS_PROJECT: str = ""
    CLOUD_TASKS_LOCATION: str = ""
    CLOUD_TASKS_QUEUE: str = ""
    CLOUD_TASKS_WORKER_URL: str = ""
    CLOUD_TASKS_OIDC_SERVICE_ACCOUNT: str = ""
    CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS: int = 3600
    WORKER_OIDC_AUDIENCE: str = ""
    WORKER_SKIP_OIDC_VERIFICATION: bool = False

    # Google Cloud
    GOOGLE_CLOUD_PROJECT: str = Field(
        validation_alias=AliasChoices("GOOGLE_CLOUD_PROJECT", "GOOGLE_PROJECT_ID"),
    )
    GOOGLE_CLOUD_LOCATION: str = Field(
        validation_alias=AliasChoices(
            "GOOGLE_CLOUD_LOCATION", "GOOGLE_PROJECT_LOCATION"
        ),
    )
    # Region for Vertex AI model inference (Gemini) specifically. Distinct
    # from GOOGLE_CLOUD_LOCATION, which anchors project-scoped infra (Cloud
    # Tasks queue location, GCS bucket location, BigQuery-adjacent defaults).
    # A project's infra region (e.g. europe-west1) does not have to match
    # the region a given Gemini model is served from / priced in (e.g.
    # europe-west3 for gemini-3.5-flash per the mounted pricing_catalog.json).
    # Defaults to GOOGLE_CLOUD_LOCATION when unset so existing single-region
    # deployments are unaffected.
    VERTEX_AI_LOCATION: str = ""
    GOOGLE_GENAI_USE_VERTEXAI: bool
    GOOGLE_CLOUD_QUOTA_PROJECT: str

    # BigQuery
    BIGQUERY_DATASET: str
    BIGQUERY_TABLE: str
    BIGQUERY_COST_ATTRIBUTION_TABLE: str
    BIGQUERY_AGENT_TELEMETRY_TABLE: str
    BIGQUERY_USER_FEEDBACK_TABLE: str

    # Firestore (search query cache)
    FIRESTORE_DATABASE: str = "(default)"
    FIRESTORE_SEARCH_CACHE_COLLECTION: str = "search_cache"

    # Redis (search query cache & content store) — PSC Memorystore
    REDIS_HOST: str = ""
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int = 0
    REDIS_TLS_ENABLED: bool = True
    # Memorystore Redis Cluster (PSC) uses a Google-managed per-instance CA
    # that is not in the system trust store by default. Set to False only
    # for ad-hoc verification against such an instance without installing
    # its CA bundle; production should keep this True.
    REDIS_TLS_VERIFY_CERT: bool = True
    REDIS_KEY_PREFIX: str = "salesagent:search:"
    REDIS_CONNECT_TIMEOUT_SECONDS: float = 2.0
    SEARCH_CACHE_BACKEND: str = "redis"  # "redis" | "firestore" | "none"
    SEARCH_CACHE_TTL_SECONDS: int = 604800  # 7 days
    SEARCH_CONCURRENCY_LIMIT: int = 8
    TOTAL_KEYWORD_BUDGET: int = 30

    # Search executor QPS/retry (see IMPLEMENTATION_PLAN.md section 6)
    SEARCH_QPS: float = 4.0
    SEARCH_QPS_BURST: int = 8
    SEARCH_TIMEOUT_SECONDS: float = 60.0
    SEARCH_QUERY_RETRY_ATTEMPTS: int = 3
    SEARCH_MIN_SUCCESS_RATE: float = 0.6

    # Per-agent retry policies for the pipeline steps
    PLANNER_RETRY_ATTEMPTS: int = 3
    ALIGNMENT_RETRY_ATTEMPTS: int = 2
    # 3 total attempts: compile -> validate -> (fail) -> revise w/ feedback
    # as this same step's next attempt -> validate -> (fail) -> revise
    # again -> validate -> (fail) -> give up. See ReportCompiler.to_input()
    # / execute() in agents/compiler.py for the revision-feedback loop.
    COMPILER_RETRY_ATTEMPTS: int = 3
    COMPILER_TIMEOUT_SECONDS: float = 300.0
    SEARCH_STEP_TIMEOUT_SECONDS: float = 300.0

    # Mounted Assets (pricing catalog, Colt product catalog)
    ASSETS_ROOT: str = ""
    PRICING_CATALOG_FILENAME: str = "pricing_catalog.json"
    COLT_CATALOG_FILENAME: str = "ColtProductCatalog.pdf"

    # Security
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    IAP_AUDIENCE: str = ""
    HUB_IAP_AUDIENCE: str = ""
    # Entra security group required for Sales Agent entitlement (checked against IAP JWT `groups` claim).
    SALES_REQUIRED_GROUP: str = ""
    # Hard ceiling on a sliding session's total lifetime, measured from the
    # `auth_time` claim stamped at the original IAP login and preserved
    # unchanged across every renewal. POST /auth/refresh refuses to mint past
    # this point, so 8 hours after signing in the user must authenticate with
    # IAP again regardless of how continuously active they have been.
    SESSION_ABSOLUTE_MAX_MINUTES: int = 480
    # When False (rollout default), a token carrying NO `scopes` claim is
    # accepted for backward compatibility with sessions minted before scopes
    # existed; a token that *has* `scopes` must still include this service's
    # own scope. Flip to True once every legacy token has expired -- that is
    # the step that actually closes the cross-service bypass.
    REQUIRE_SCOPE_CLAIM: bool = False
    PROMPT_TEMPLATE_VERSION: str = "v1.0"

    # GCS
    GCS_BUCKET_NAME: str
    GCS_SIGNED_URL_EXPIRATION_HOURS: int = 1
    GCS_PARENT_FOLDER: str = "salesagent_response"

    # CORS
    CORS_ALLOW_ORIGINS: list[str] = ["*"]
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: list[str] = ["*"]
    CORS_ALLOW_HEADERS: list[str] = ["*"]

    # OpenTelemetry
    OTEL_ENABLED: bool = True
    OTEL_SERVICE_NAME: str = "sales-agent-api"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "https://telemetry.googleapis.com/v1/traces"
    OTEL_EXPORTER_OTLP_PROTOCOL: str = "http/protobuf"
    OTEL_RESOURCE_ATTRIBUTES: str = ""
    OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED: bool = True
    OTEL_SEMCONV_STABILITY_OPT_IN: str = "gen_ai_latest_experimental"
    OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT: str = "EVENT_ONLY"
    ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS: bool = False

    # Research / Gemini Models
    LLM_MODEL: str = Field(
        default="gemini-3.5-flash",
        validation_alias=AliasChoices("LLM_MODEL", "GEMINI_MODEL"),
    )
    SEARCH_MODEL: str = Field(
        default="gemini-3.5-flash",
        validation_alias=AliasChoices("SEARCH_MODEL", "SEARCH_AGENT_MODEL"),
    )
    EVALUATOR_MODEL: str = ""
    OUTPUT_GUARDRAIL_HALLUCINATION_MODEL: str = ""
    AGENT_COMPACT_SUMMARIZER_MODEL: str = ""
    RESEARCH_STATUS_MIN_UPDATE_INTERVAL_SECONDS: float = 5.0
    GEMINI_RETRY_ATTEMPTS: int = 3
    GEMINI_RETRY_INITIAL_DELAY: int = 5
    GEMINI_RETRY_MAX_DELAY: int = 120
    GEMINI_RETRY_EXP_BASE: int = 2
    GEMINI_RETRY_JITTER: int = 1
    GEMINI_RETRY_STATUS_CODES: list[int] = [408, 429, 500, 502, 503, 504]
    # Output cap for agents that emit one large structured payload
    AGENT_MAX_OUTPUT_TOKENS: int = 65_535
    # Minimum per-domain research outputs (of 12) required before synthesis.
    RESEARCH_MIN_DOMAIN_OUTPUTS: int = 6
    RESEARCH_ABORT_ON_MISSING_DOMAINS: bool = False
    AGENT_EVENTS_COMPACT_ENABLED: bool = True
    AGENT_EVENTS_COMPACT_TOKEN_THRESHOLD: int = 100_000
    AGENT_EVENTS_COMPACT_RETENTION: int = 6
    AGENT_EVENTS_COMPACT_INTERVAL: int = 3
    AGENT_EVENTS_COMPACT_OVERLAP: int = 1
    EVAL_EMBEDDING_ENABLED: bool = False
    EVAL_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EVAL_EMBEDDING_ONNX_PATH: str = "models/all-MiniLM-L6-v2/onnx/model.onnx"
    EVAL_EMBEDDING_SIMILARITY_THRESHOLD: float = 0.55
    AGENT_RETRY_ATTEMPTS: int = 3
    AGENT_RETRY_WAIT_FIXED: int = 2
    JOB_ID_PREFIX: str = "job_"
    RESEARCH_INIT_STEP_LABEL: str = "Initializing research"
    RESEARCH_INIT_PROGRESS: int = 5
    RESEARCH_UPLOAD_STEP_LABEL: str = "Uploading artifacts"
    RESEARCH_UPLOAD_PROGRESS: int = 92
    RESEARCH_EVAL_STEP_LABEL: str = "Running quality evaluation"
    RESEARCH_EVAL_PROGRESS: int = 97

    # Safety
    SAFETY_HARASSMENT_THRESHOLD: str = "BLOCK_MEDIUM_AND_ABOVE"
    SAFETY_HATE_SPEECH_THRESHOLD: str = "BLOCK_MEDIUM_AND_ABOVE"
    SAFETY_SEXUAL_THRESHOLD: str = "BLOCK_LOW_AND_ABOVE"
    SAFETY_DANGEROUS_THRESHOLD: str = "BLOCK_ONLY_HIGH"
    SAFETY_LOGGING_ENABLED: bool = True

    # Output guardrails
    OUTPUT_GUARDRAIL_MIN_SECTIONS: int = 8
    OUTPUT_GUARDRAIL_MAX_RETRIES: int = 2
    OUTPUT_GUARDRAIL_HALLUCINATION_BLOCK_THRESHOLD: int = 5
    # Second, stronger ReportCompiler retry gate: BM25-scores each factual
    # sentence in the drafted report against the real Google Search
    # grounding evidence (SearchFindings.all_evidence()) via Bm25Verifier.
    # A structurally-perfect report (passes OUTPUT_GUARDRAIL checks) with
    # mostly ungrounded/hallucinated claims still fails this gate and
    # triggers a ReportCompiler revision retry. Kept as a toggle so it can
    # be disabled without a code change if it proves too strict in
    # production.
    REPORT_COMPILER_BM25_GATE_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=_DOTENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @field_validator(
        "LOG_FILE",
        "AGENT_EVENT_LOG_FILE",
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
    def bigquery_table_ref(self) -> str:
        return (
            f"{self.GOOGLE_CLOUD_PROJECT}.{self.BIGQUERY_DATASET}.{self.BIGQUERY_TABLE}"
        )

    @property
    def gcs_bucket_uri(self) -> str:
        return f"gs://{self.GCS_BUCKET_NAME}"

    @property
    def assets_root_path(self) -> Path:
        """Resolve the assets directory (Cloud Run mount /secrets/assets vs local)."""
        if self.ASSETS_ROOT:
            return resolve_repo_path(self.ASSETS_ROOT)
        if not self.IS_LOCAL:
            return Path("/secrets/assets")
        # Local fallback resolution
        for candidate in [
            _REPO_ROOT / ".local-tmp" / "assets-cache",
            _REPO_ROOT / "assets",
            _REPO_ROOT / "data",
            _REPO_ROOT,
        ]:
            if (candidate / self.PRICING_CATALOG_FILENAME).exists():
                return candidate
        return _REPO_ROOT / ".local-tmp" / "assets-cache"

    @property
    def pricing_catalog_path(self) -> Path:
        """Path to the mounted pricing catalog JSON file."""
        direct = self.assets_root_path / self.PRICING_CATALOG_FILENAME
        if direct.exists() or not self.IS_LOCAL:
            return direct
        # Local-only fallback if stored under data/ or assets/
        for parent in [_REPO_ROOT / "data", _REPO_ROOT / "assets", _REPO_ROOT]:
            candidate = parent / self.PRICING_CATALOG_FILENAME
            if candidate.exists():
                return candidate
        return direct

    @property
    def colt_catalog_path(self) -> Path:
        """Path to the mounted Colt product catalog PDF."""
        direct = self.assets_root_path / self.COLT_CATALOG_FILENAME
        if direct.exists() or not self.IS_LOCAL:
            return direct
        # Local-only fallback if stored in repo root or data/ or assets/
        for parent in [_REPO_ROOT, _REPO_ROOT / "data", _REPO_ROOT / "assets"]:
            candidate = parent / self.COLT_CATALOG_FILENAME
            if candidate.exists():
                return candidate
        return direct

    @property
    def GEMINI_MODEL(self) -> str:
        """Alias for LLM_MODEL."""
        return self.LLM_MODEL

    @property
    def SEARCH_AGENT_MODEL(self) -> str:
        """Alias for SEARCH_MODEL."""
        return self.SEARCH_MODEL

    @property
    def evaluator_model(self) -> str:
        return self.EVALUATOR_MODEL or self.LLM_MODEL

    @property
    def output_guardrail_hallucination_model(self) -> str:
        return self.OUTPUT_GUARDRAIL_HALLUCINATION_MODEL or self.LLM_MODEL

    @property
    def agent_compact_summarizer_model(self) -> str:
        return self.AGENT_COMPACT_SUMMARIZER_MODEL or self.SEARCH_MODEL

    @property
    def vertex_ai_location(self) -> str:
        """Region used for Vertex AI Gemini inference calls specifically.

        Falls back to GOOGLE_CLOUD_LOCATION when VERTEX_AI_LOCATION is not
        set, so single-region deployments need no config change. Set
        VERTEX_AI_LOCATION explicitly when the LLM-serving region differs
        from the project's infra region (Cloud Tasks/GCS/BigQuery).
        """
        return self.VERTEX_AI_LOCATION or self.GOOGLE_CLOUD_LOCATION

    @property
    def llm_model_info(self) -> Any:
        """Resolved ModelInfo object from mounted pricing catalog for primary LLM."""
        from src.shared.model_registry import get_model_registry

        return get_model_registry().get_model(
            self.LLM_MODEL, region=self.vertex_ai_location
        )

    @property
    def search_model_info(self) -> Any:
        """Resolved ModelInfo object from mounted pricing catalog for Search model."""
        from src.shared.model_registry import get_model_registry

        return get_model_registry().get_model(
            self.SEARCH_MODEL, region=self.vertex_ai_location
        )

    def validate_mounted_assets(self) -> dict[str, Path]:
        """Validate required mounted assets at startup; raise RuntimeError if missing."""
        missing: list[str] = []
        pricing_path = self.pricing_catalog_path
        if not pricing_path.is_file():
            missing.append(f"{self.PRICING_CATALOG_FILENAME} (checked: {pricing_path})")

        colt_path = self.colt_catalog_path
        if not colt_path.is_file():
            missing.append(f"{self.COLT_CATALOG_FILENAME} (checked: {colt_path})")

        if missing:
            raise RuntimeError(
                f"Required mounted assets missing at startup: {', '.join(missing)}. "
                f"Ensure assets are mounted to ASSETS_ROOT ({self.assets_root_path}) "
                "before spinning up the service."
            )
        return {
            "pricing_catalog": pricing_path,
            "colt_catalog": colt_path,
        }


settings = Settings()
