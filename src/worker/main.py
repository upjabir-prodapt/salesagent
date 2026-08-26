"""Worker Application Module — Cloud Tasks consumer for Sales Agent jobs."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import NoOpTracerProvider

from src.shared.config import settings
from src.shared.logging_config import logger, setup_logging
from src.shared.middlewares import error_handler_middleware, logging_middleware
from src.shared.otel_setup import setup_telemetry, shutdown_telemetry

from .api.health import router as health_router
from .api.routes import router as tasks_router

try:
    from opentelemetry.instrumentation.google_genai import GoogleGenAiSdkInstrumentor
except ImportError:  # pragma: no cover - optional dependency
    GoogleGenAiSdkInstrumentor = None

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Worker application lifespan events."""
    logger.info(f"Starting {settings.APP_NAME} Worker v{settings.APP_VERSION}")
    logger.info(f"Environment: {'DEBUG' if settings.DEBUG else 'PRODUCTION'}")
    logger.info(f"Google Cloud Project: {settings.GOOGLE_CLOUD_PROJECT}")

    # 1. Lifecycle preflight: Validate and pre-load required mounted assets
    logger.info("Validating required mounted assets during startup lifecycle...")
    try:
        mounted_assets = settings.validate_mounted_assets()

        # Pre-load pricing catalog
        from src.shared.model_registry import get_model_registry

        registry = get_model_registry()
        logger.info(
            "Pricing catalog verified: %d model configuration(s) loaded",
            len(registry.models),
        )

        # Pre-load Colt product catalog PDF
        from src.worker.agents.tools.gcs_pdf_loader import (
            load_mounted_colt_catalog_pdf,
        )

        catalog_text = load_mounted_colt_catalog_pdf()
        logger.info(
            "Colt product catalog verified: %d characters extracted",
            len(catalog_text) if catalog_text else 0,
        )

        logger.info(
            "Mounted assets verified and pre-warmed: %s",
            {k: str(v) for k, v in mounted_assets.items()},
        )
    except Exception as e:
        logger.critical("FATAL: Asset preflight failed during startup lifecycle: %s", e)
        raise RuntimeError(
            f"Worker startup aborted due to missing/invalid mounted assets: {e}"
        ) from e

    try:
        _init_telemetry(app)
    except Exception:
        logger.exception("Error during worker telemetry initialization")
        if not settings.DEBUG:
            raise
        logger.warning(
            "Continuing worker startup in DEBUG mode despite telemetry failures."
        )

    yield

    if settings.OTEL_ENABLED:
        shutdown_telemetry()
    logger.info("Shutting down worker application")


def _init_telemetry(app: FastAPI) -> None:
    """Bootstrap OTLP → Cloud Trace and HTTP/LLM auto-instrumentation."""
    try:
        if not settings.OTEL_ENABLED:
            trace.set_tracer_provider(NoOpTracerProvider())
            logger.info(
                "OpenTelemetry export disabled (OTEL_ENABLED=false); "
                "running with no-op tracer provider."
            )
            return

        logger.info("Initializing OpenTelemetry (OTLP → Cloud Trace)...")
        setup_telemetry()
        FastAPIInstrumentor.instrument_app(app)
        if GoogleGenAiSdkInstrumentor is not None:
            GoogleGenAiSdkInstrumentor().instrument()
        logger.info("OpenTelemetry initialized successfully on worker")
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry on worker: {e}")
        raise


app = FastAPI(
    title=f"{settings.APP_NAME} Worker",
    version=settings.APP_VERSION,
    description="Cloud Tasks Worker for Sales Intelligence Research Pipeline",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    debug=settings.DEBUG,
)

error_handler_middleware(app)
logging_middleware(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(tasks_router)


@app.get("/", tags=["Root"])
async def root():
    """Worker root endpoint."""
    return {
        "service": "sales-agent-worker",
        "version": settings.APP_VERSION,
        "status": "running",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Custom 404 handler."""
    return JSONResponse(
        status_code=404,
        content={
            "error": "NOT_FOUND",
            "message": f"The requested URL {request.url.path} was not found",
            "correlation_id": getattr(request.state, "correlation_id", "unknown"),
        },
    )
