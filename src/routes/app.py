"""Main Application Module"""

from contextlib import asynccontextmanager

import google.auth
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.trace import NoOpTracerProvider

from ..core.config import settings
from ..core.otel_setup import setup_telemetry, shutdown_telemetry
from ..core.logging_config import logger, setup_logging
from ..middlewares import error_handler_middleware, logging_middleware
from . import auth, catalog, research

try:
    from opentelemetry.instrumentation.google_genai import GoogleGenAiSdkInstrumentor
except ImportError:  # pragma: no cover - optional dependency
    GoogleGenAiSdkInstrumentor = None

# Configure logging
setup_logging()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""

    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {'DEBUG' if settings.DEBUG else 'PRODUCTION'}")
    logger.info(f"Google Cloud Project: {settings.GOOGLE_CLOUD_PROJECT}")

    try:
        await _init_bigquery()
        await _init_gcs()
        # OTel FIRST — ADK Runner.run_async() uses the global TracerProvider lazily;
        # setting it here before any request guarantees ADK spans reach Cloud Trace.
        _init_telemetry(app)
    except Exception:
        logger.exception("Error during resource initialization")
        if not settings.DEBUG:
            raise
        logger.warning(
            "Continuing startup in DEBUG mode despite initialization failures."
        )

    yield

    if settings.OTEL_ENABLED:
        shutdown_telemetry()
    logger.info("Shutting down application")


async def _init_bigquery() -> None:
    """Reserved startup hook for BigQuery initialization."""


async def _init_gcs() -> None:
    """Reserved startup hook for GCS initialization."""


def get_gcp_exporters() -> dict[str, str]:
    """Compatibility hook for telemetry exporter config."""
    return {"otlp_endpoint": settings.OTEL_EXPORTER_OTLP_ENDPOINT}


def get_gcp_resource(project_id: str | None) -> dict[str, str]:
    """Compatibility hook for telemetry resource config."""
    return {"project_id": project_id or settings.GOOGLE_CLOUD_PROJECT}


def _default_set_otel_providers(
    exporters: dict[str, str], resource: dict[str, str]
) -> None:
    del exporters, resource
    setup_telemetry()


maybe_set_otel_providers = _default_set_otel_providers


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
        _, project_id = google.auth.default()
        exporters = get_gcp_exporters()
        resource = get_gcp_resource(project_id)
        maybe_set_otel_providers(exporters, resource)
        FastAPIInstrumentor.instrument_app(app)
        if (
            maybe_set_otel_providers is not _default_set_otel_providers
            and GoogleGenAiSdkInstrumentor is not None
        ):
            GoogleGenAiSdkInstrumentor().instrument()
        logger.info("OpenTelemetry initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry: {e}")
        raise


# Create FastAPI app
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Professional Sales Intelligence Research API powered by Google ADK",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    debug=settings.DEBUG,
)

# Add middlewares (order matters - error handler should wrap everything)
error_handler_middleware(app)
logging_middleware(app)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ALLOW_ORIGINS,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=settings.CORS_ALLOW_METHODS,
    allow_headers=settings.CORS_ALLOW_HEADERS,
)

# Include routers
app.include_router(auth.router)
app.include_router(research.router)
app.include_router(catalog.router)


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with API information"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "description": "Professional Sales Intelligence Research API",
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc",
            "openapi": "/openapi.json",
        },
        "endpoints": {
            "research": "/api/v1/research",
            "catalog": "/api/v1/catalog",
            "health": "/health",
            "metrics": "/metrics",
        },
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": "DEBUG" if settings.DEBUG else "PRODUCTION",
    }


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """Custom 404 handler"""
    return JSONResponse(
        status_code=404,
        content={
            "error": "NOT_FOUND",
            "message": f"The requested URL {request.url.path} was not found",
            "correlation_id": getattr(request.state, "correlation_id", "unknown"),
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.routes.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=settings.WORKERS if not settings.DEBUG else 1,
    )
