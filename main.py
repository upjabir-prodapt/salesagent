"""
Main entry point for the FastAPI application.

Usage:
    python main.py

Or with uvicorn directly:
    uvicorn main:app --reload
"""

import uvicorn

from src.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "src.routes.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        workers=settings.WORKERS if not settings.DEBUG else 1,
        log_level=settings.LOG_LEVEL.lower(),
    )
