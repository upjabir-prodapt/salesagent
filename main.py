"""
Main entry point for the FastAPI application.

Usage:
    python main.py

Or with uvicorn directly:
    uvicorn main:app --reload
"""

import os

import uvicorn

from src.core.config import settings
from src.routes.app import app


def main() -> None:
    port = int(os.environ.get("PORT", 8080))
    host = settings.HOST
    print(f"Starting server on {host}:{port}...", flush=True)
    uvicorn.run(app, host=host, port=port, log_level=settings.LOG_LEVEL.lower())


if __name__ == "__main__":
    main()
