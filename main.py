"""
Main entry point for the FastAPI application (backward-compatible alias for main_api.py).

Usage:
    python main.py (or python main_api.py)

Or with uvicorn directly:
    uvicorn main:app --reload
"""

import os

import uvicorn

from src.shared.config import settings


def main() -> None:
    port = int(os.environ.get("PORT", 8080))
    host = settings.HOST
    print(f"Starting server on {host}:{port}...", flush=True)
    uvicorn.run(
        "src.api.main:app", host=host, port=port, log_level=settings.LOG_LEVEL.lower()
    )


if __name__ == "__main__":
    main()
