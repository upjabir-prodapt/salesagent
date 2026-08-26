"""
Main entry point for the internal Sales Agent Worker Application (Worker role).

Usage:
    python main_worker.py

Or with uvicorn directly:
    uvicorn main_worker:app --reload
"""

import os

import uvicorn

from src.shared.config import settings


def main() -> None:
    port = int(os.environ.get("PORT", settings.PORT))
    host = settings.HOST
    print(f"Starting Worker server on {host}:{port}...", flush=True)
    uvicorn.run(
        "src.worker.main:app",
        host=host,
        port=port,
        log_level=settings.LOG_LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
