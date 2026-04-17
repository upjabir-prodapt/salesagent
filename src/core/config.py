"""Configuration Management using Pydantic Settings"""

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    # API Configuration
    APP_NAME: str = Field(default="Sales Research API", description="Application name")
    APP_VERSION: str = Field(default="1.0.0", description="Application version")
    API_PREFIX: str = Field(default="/api/v1", description="API route prefix")
    DEBUG: bool = Field(default=False, description="Debug mode")

    # Server Configuration
    HOST: str = Field(default="0.0.0.0", description="Server host")
    PORT: int = Field(default=8000, description="Server port")
    WORKERS: int = Field(default=1, description="Number of workers")

    # Google Cloud Configuration
    GOOGLE_CLOUD_PROJECT: str = Field(
        default="cloud-practice-dev-2", description="Google Cloud project ID"
    )
    GOOGLE_CLOUD_LOCATION: str = Field(
        default="us-central1", description="Google Cloud region for resources"
    )

    # BigQuery Configuration
    BIGQUERY_DATASET: str = Field(default="colt_ingest", description="BigQuery dataset")
    BIGQUERY_TABLE: str = Field(
        default="salesagent_requests", description="BigQuery table"
    )
    BIGQUERY_MODEL_CARD_TABLE: str = Field(
        default="salesagent_model_cards", description="BigQuery model card table"
    )
    BIGQUERY_AGENT_TELEMETRY_TABLE: str = Field(
        default="agent_telemetry", description="BigQuery per-agent telemetry table"
    )
    PROMPT_TEMPLATE_VERSION: str = Field(
        default="v1.0", description="Version of the prompt template used by agents"
    )

    # GCS Configuration
    GCS_BUCKET_NAME: str = Field(
        default="colt-ai-usecase", description="GCS bucket name"
    )
    GCS_SIGNED_URL_EXPIRATION_HOURS: int = Field(
        default=1, description="Signed URL expiration in hours"
    )
    GCS_PARENT_FOLDER: str = Field(
        default="salesagent_response", description="Parent folder for GCS"
    )

    # CORS Configuration
    CORS_ALLOW_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8000"],
        description="Allowed CORS origins",
    )
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True, description="Allow credentials")
    CORS_ALLOW_METHODS: list[str] = Field(default=["*"], description="Allowed methods")
    CORS_ALLOW_HEADERS: list[str] = Field(default=["*"], description="Allowed headers")

    # Logging Configuration
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")

    # Research Configuration
    GEMINI_MODEL: str = Field(
        default="gemini-2.5-pro", description="Advanced GenAI model"
    )
    GEMINI_RETRY_ATTEMPTS: int = Field(
        default=3, description="Number of retry attempts"
    )
    GEMINI_RETRY_INITIAL_DELAY: int = Field(
        default=5, description="Delay between retries in seconds"
    )
    GEMINI_RETRY_MAX_DELAY: int = Field(
        default=60, description="Max delay between retries in seconds"
    )
    GEMINI_RETRY_EXP_BASE: int = Field(
        default=2, description="Backoff factor for retries"
    )
    GEMINI_RETRY_JITTER: int = Field(default=1, description="Jitter factor for retries")
    GEMINI_RETRY_STATUS_CODES: list[int] = Field(
        default=[408, 429, 500, 502, 503, 504],
        description="HTTP status codes to retry on",
    )

    # Safety Settings - Content Guardrails
    SAFETY_HARASSMENT_THRESHOLD: str = Field(
        default="BLOCK_MEDIUM_AND_ABOVE",
        description="Safety threshold for harassment content",
    )
    SAFETY_HATE_SPEECH_THRESHOLD: str = Field(
        default="BLOCK_MEDIUM_AND_ABOVE",
        description="Safety threshold for hate speech",
    )
    SAFETY_SEXUAL_THRESHOLD: str = Field(
        default="BLOCK_LOW_AND_ABOVE",
        description="Safety threshold for sexually explicit content",
    )
    SAFETY_DANGEROUS_THRESHOLD: str = Field(
        default="BLOCK_ONLY_HIGH",
        description="Safety threshold for dangerous content (relaxed for business research)",
    )
    SAFETY_LOGGING_ENABLED: bool = Field(
        default=True, description="Enable safety event logging"
    )
    AGENT_RETRY_ATTEMPTS: int = Field(default=3, description="Number of retry attempts")
    AGENT_RETRY_WAIT_FIXED: int = Field(
        default=2, description="Delay between retries in seconds"
    )

    # Job ID config
    JOB_ID_PREFIX: str = Field(
        default="job_", description="Prefix for generated job IDs"
    )

    # Progress milestone config (maps agent name → [progress_pct, step_label])
    AGENT_PROGRESS_MAP: str = Field(
        default='{"ResearchOrchestrator": [10, "Research Orchestrator: Gathering intelligence"], "AlignmentAnalyst": [75, "Alignment Analyst: Mapping solutions"], "ReportCompiler": [90, "Report Compiler: Generating final report"]}',
        description="JSON map of agent name to [progress_pct, step_label]",
    )

    # Model cost — Gemini 2.5 Pro standard rates (non-cached, ≤200K context)
    # Input:  $1.25 / 1M tokens  = $0.00125 / 1K tokens
    # Output: $10.00 / 1M tokens = $0.01000 / 1K tokens
    GEMINI_COST_PER_1K_INPUT_TOKENS: float = Field(
        default=0.00125, description="Cost per 1K input tokens in USD (Gemini 2.5 Pro)"
    )
    GEMINI_COST_PER_1K_OUTPUT_TOKENS: float = Field(
        default=0.01, description="Cost per 1K output tokens in USD (Gemini 2.5 Pro)"
    )

    # Research progress labels and percentages
    RESEARCH_INIT_STEP_LABEL: str = Field(
        default="Initializing research", description="Label for initial progress step"
    )
    RESEARCH_INIT_PROGRESS: int = Field(
        default=5, description="Initial progress percentage on PROCESSING start"
    )
    RESEARCH_UPLOAD_STEP_LABEL: str = Field(
        default="Uploading artifacts", description="Label for upload step"
    )
    RESEARCH_UPLOAD_PROGRESS: int = Field(
        default=92, description="Progress pct after agent completes, before upload"
    )
    RESEARCH_EVAL_STEP_LABEL: str = Field(
        default="Running quality evaluation", description="Label for evaluation step"
    )
    RESEARCH_EVAL_PROGRESS: int = Field(
        default=97, description="Progress pct during evaluation"
    )

    # Evaluation Configuration
    EVALUATOR_MODEL: str = Field(
        default="gemini-2.0-flash", description="LLM model used as evaluation judge"
    )
    COLT_PRODUCT_CATALOG_PATH: str = Field(
        default="ColtProductCatalog.pdf",
        description="Path to Colt Product Catalog PDF (relative to project root)",
    )
    BERTSCORE_MODEL: str = Field(
        default="distilbert-base-uncased",
        description="HuggingFace model used for BERTScore computation",
    )

    # Output Guardrail Configuration
    OUTPUT_GUARDRAIL_HALLUCINATION_MODEL: str = Field(
        default="gemini-2.0-flash",
        description="Secondary LLM model for output hallucination cross-reference check (Section 11 vs Section 12)",
    )
    OUTPUT_GUARDRAIL_MIN_SECTIONS: int = Field(
        default=10,
        description="Minimum populated sections required (out of 13) to pass the completeness check",
    )
    OUTPUT_GUARDRAIL_MAX_RETRIES: int = Field(
        default=2,
        description="Max additional report generation attempts if output guardrails fail (0 = no retry)",
    )
    OUTPUT_GUARDRAIL_HALLUCINATION_BLOCK_THRESHOLD: int = Field(
        default=2,
        description=(
            "Minimum number of unsupported claims required before the hallucination check "
            "blocks the report. Default 2 — a single borderline claim does not fail the job."
        ),
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )

    @property
    def agent_progress_map(self) -> dict[str, tuple[int, str]]:
        """Parse AGENT_PROGRESS_MAP JSON into agent_name → (progress_pct, step_label)"""
        import json

        raw = json.loads(self.AGENT_PROGRESS_MAP)
        return {k: (v[0], v[1]) for k, v in raw.items()}

    @property
    def bigquery_table_ref(self) -> str:
        """Get full BigQuery table reference"""
        return (
            f"{self.GOOGLE_CLOUD_PROJECT}.{self.BIGQUERY_DATASET}.{self.BIGQUERY_TABLE}"
        )

    @property
    def gcs_bucket_uri(self) -> str:
        """Get GCS bucket URI"""
        return f"gs://{self.GCS_BUCKET_NAME}"


# Create settings instance
settings = Settings()
