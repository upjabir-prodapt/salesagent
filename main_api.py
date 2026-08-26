"""
Main entry point for the public Sales Agent FastAPI Application (API role).

Usage:
    python main_api.py

Or with uvicorn directly:
    uvicorn main_api:app --reload
"""

import os

import uvicorn

from src.shared.config import settings


def main() -> None:
    port = int(os.environ.get("PORT", settings.PORT))
    host = settings.HOST
    print(f"Starting API server on {host}:{port}...", flush=True)
    uvicorn.run(
        "src.api.main:app", host=host, port=port, log_level=settings.LOG_LEVEL.lower()
    )


if __name__ == "__main__":
    main()
