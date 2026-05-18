"""Main Application Module"""

import asyncio

# OpenTelemetry imports
from contextlib import asynccontextmanager

import google.auth
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google.adk.telemetry.google_cloud import get_gcp_exporters, get_gcp_resource
from google.adk.telemetry.setup import maybe_set_otel_providers
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.google_genai import GoogleGenAiSdkInstrumentor
from opentelemetry.trace import NoOpTracerProvider

from ..core.config import settings
from ..core.exceptions import ConfigurationError
from ..core.logging_config import logger, setup_logging
from ..dependencies.service_dependencies import (
    get_bigquery_repository,
    get_gcs_repository,
)
from ..middlewares import error_handler_middleware, logging_middleware
from . import auth, research

# Configure logging
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {'DEBUG' if settings.DEBUG else 'PRODUCTION'}")
    logger.info(f"Google Cloud Project: {settings.GOOGLE_CLOUD_PROJECT}")

    _validate_runtime_config()

    try:
        await asyncio.gather(_init_bigquery(), _init_gcs())
        _init_telemetry(app)
    except Exception:
        logger.exception("Error during resource initialization")
        if not settings.DEBUG:
            raise
        logger.warning(
            "Continuing startup in DEBUG mode despite initialization failures."
        )

    yield

    logger.info("Shutting down application")


async def _init_bigquery():
    # Ensure BigQuery dataset and jobs table exist
    try:
        logger.info("Initializing BigQuery table...")
        bigquery_repo = get_bigquery_repository()
        await asyncio.to_thread(bigquery_repo.ensure_table_exists)
        await asyncio.to_thread(bigquery_repo.ensure_cost_attribution_table_exists)
        await asyncio.to_thread(bigquery_repo.ensure_agent_telemetry_table_exists)
        await asyncio.to_thread(bigquery_repo.ensure_users_table_exists)
        logger.info("BigQuery table initialization completed successfully")
    except Exception as e:
        logger.error(f"Failed to initialize BigQuery table: {e}")
        raise


async def _init_gcs():
    """Initialize GCS bucket"""
    try:
        logger.info("Initializing GCS bucket...")
        gcs_repo = get_gcs_repository()
        await asyncio.to_thread(gcs_repo.ensure_bucket_exists)
        logger.info("GCS bucket initialization completed successfully")
    except Exception as e:
        logger.error(f"Failed to initialize GCS bucket: {e}")
        raise


def _init_telemetry(app: FastAPI):
    """Initialize OpenTelemetry using ADK otel_to_cloud primitives."""
    try:
        if not settings.OTEL_ENABLED:
            trace.set_tracer_provider(NoOpTracerProvider())
            logger.info(
                "OpenTelemetry export disabled (OTEL_ENABLED=false); "
                "running with no-op tracer provider."
            )
            return

        logger.info("Initializing OpenTelemetry...")
        credentials, project_id = google.auth.default()
        hooks = get_gcp_exporters(
            enable_cloud_tracing=True,
            enable_cloud_metrics=True,
            enable_cloud_logging=True,
            google_auth=(credentials, project_id),
        )
        resource = get_gcp_resource(project_id)
        maybe_set_otel_providers(
            otel_hooks_to_setup=[hooks],
            otel_resource=resource,
        )

        # App-level HTTP instrumentation is not done automatically by ADK web bootstrap.
        FastAPIInstrumentor.instrument_app(app)
        try:
            GoogleGenAiSdkInstrumentor().instrument()
        except Exception as instrument_error:
            logger.warning(f"Unable to instrument google-genai SDK: {instrument_error}")
        logger.info("OpenTelemetry initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize OpenTelemetry: {e}")
        raise


def _validate_runtime_config() -> None:
    """Fail fast on unsafe runtime configuration."""
    if (
        not settings.DEBUG
        and settings.AUTH_ENABLED
        and settings.SECRET_KEY == settings.DEFAULT_INSECURE_SECRET_KEY
    ):
        raise ConfigurationError(
            "Unsafe configuration: default SECRET_KEY is not allowed in production."
        )


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
