"""Main Application Module"""

import asyncio

# OpenTelemetry imports
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.instrumentation.google_genai import GoogleGenAiSdkInstrumentor
from opentelemetry.instrumentation.sqlite3 import SQLite3Instrumentor
from opentelemetry.instrumentation.vertexai import VertexAIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from ..core.config import settings
from ..core.logging_config import setup_logging
from ..dependencies.service_dependencies import (
    get_bigquery_repository,
    get_gcs_repository,
)
from ..middlewares import error_handler_middleware, logging_middleware
from . import research

# Load environment variables from .env file
load_dotenv()
load_dotenv("/secrets/.env", override=True)

# Configure logging
setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events"""
    # Startup
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}")
    logger.info(f"Environment: {'DEBUG' if settings.DEBUG else 'PRODUCTION'}")
    logger.info(f"Google Cloud Project: {settings.GOOGLE_CLOUD_PROJECT}")

    # Initialize resources concurrently
    try:
        await asyncio.gather(_init_bigquery(), _init_gcs())
        _init_telemetry()
    except Exception as e:
        logger.error(f"Error during resource initialization: {e}")
        # Note: Individual failures are logged in their respective functions.
        # We catch here ensuring one failure doesn't crash the entire startup if we want graceful degradation,
        # though typically for DB/Storage we might want to fail hard.
        # Given previous code swallowed errors (with comments), we continue that pattern but log the aggregate error.

    yield

    # Shutdown
    logger.info("Shutting down application")


async def _init_bigquery():
    """Initialize BigQuery table"""
    try:
        logger.info("Initializing BigQuery table...")
        bigquery_repo = get_bigquery_repository()
        await asyncio.to_thread(bigquery_repo.ensure_table_exists)
        await asyncio.to_thread(bigquery_repo.ensure_model_card_table_exists)
        await asyncio.to_thread(bigquery_repo.ensure_agent_telemetry_table_exists)
        logger.info("BigQuery table initialization completed successfully")
    except Exception as e:
        logger.error(f"Failed to initialize BigQuery table: {e}")
        # raise # Optional: raise if critical


async def _init_gcs():
    """Initialize GCS bucket"""
    try:
        logger.info("Initializing GCS bucket...")
        gcs_repo = get_gcs_repository()
        await asyncio.to_thread(gcs_repo.ensure_bucket_exists)
        logger.info("GCS bucket initialization completed successfully")
    except Exception as e:
        logger.error(f"Failed to initialize GCS bucket: {e}")
        # raise # Optional: raise if critical


def _init_telemetry():
    """Initialize OpenTelemetry with Google Cloud Trace and instrument GenAI libraries"""
    if os.getenv("OTEL_SERVICE_NAME"):
        try:
            logger.info("Initializing OpenTelemetry...")
            # Trace Setup — CloudTraceSpanExporter sends directly to GCP Cloud Trace
            provider = TracerProvider()
            processor = BatchSpanProcessor(CloudTraceSpanExporter())
            provider.add_span_processor(processor)
            trace.set_tracer_provider(provider)

            # Auto-instrument ADK / GenAI libraries
            GoogleGenAiSdkInstrumentor().instrument()
            VertexAIInstrumentor().instrument()
            SQLite3Instrumentor().instrument()
            logger.info("OpenTelemetry initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize OpenTelemetry: {e}")


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
