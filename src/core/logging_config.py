"""Logging Configuration Module - Configures loguru for the application."""

import json
import sys

from loguru import logger

from .config import settings


def gcp_json_formatter(record):
    """
    Format logs as JSON compatible with Google Cloud Logging.
    Maps fields to logging.googleapis.com/trace and logging.googleapis.com/spanId.
    """
    # Extract trace and span IDs from context
    trace_id = record["extra"].get("trace_id")
    span_id = record["extra"].get("span_id")
    project_id = settings.GOOGLE_CLOUD_PROJECT

    # Base payload
    payload = {
        "severity": record["level"].name,
        "timestamp": record["time"].isoformat(),
        "message": record["message"],
        "logging.googleapis.com/sourceLocation": {
            "file": record["file"].path,
            "line": record["line"],
            "function": record["function"],
        },
    }

    # Add GCP Trace correlation if IDs are present
    if trace_id and trace_id != "no-trace":
        payload["logging.googleapis.com/trace"] = f"projects/{project_id}/traces/{trace_id}"
    
    if span_id:
        payload["logging.googleapis.com/spanId"] = span_id

    # Include all other extra fields
    for key, value in record["extra"].items():
        if key not in ["trace_id", "span_id"]:
            payload[key] = value

    return json.dumps(payload) + "\n"


def setup_logging():
    """
    Configure loguru based on application settings.

    Configures:
    - Console logging with color formatting (Debug/Local)
    - Structured JSON logging for GCP (Production/Cloud)
    - Log level from settings.LOG_LEVEL
    """
    # Remove default handler
    logger.remove()

    if settings.DEBUG:
        # Debug/Local: Human-readable colored output
        logger.add(
            sys.stderr,
            level=settings.LOG_LEVEL,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<magenta>{extra[trace_id]}</magenta> | "
                "<yellow>{extra[user_email]}</yellow> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                "<level>{message}</level>"
            ),
            colorize=True,
            filter=lambda record: (
                record["extra"].setdefault("trace_id", "no-trace"),
                record["extra"].setdefault("user_email", "anonymous"),
            ) or True
        )
    else:
        # Production/Cloud: Structured JSON for GCP Trace Explorer correlation
        logger.add(
            sys.stderr,
            level=settings.LOG_LEVEL,
            format=gcp_json_formatter,
            filter=lambda record: (
                record["extra"].setdefault("trace_id", "no-trace"),
                record["extra"].setdefault("span_id", None),
                record["extra"].setdefault("user_email", "anonymous"),
            ) or True
        )

    logger.info(f"Logging configured with level: {settings.LOG_LEVEL} (GCP correlation: {'OFF' if settings.DEBUG else 'ON'})")
