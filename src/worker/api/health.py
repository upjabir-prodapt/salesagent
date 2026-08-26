"""Worker health routes."""

from fastapi import APIRouter

from ...shared.config import settings

router = APIRouter()


@router.get("/health", tags=["Health"])
async def health_check():
    """Worker health check endpoint."""
    return {
        "status": "healthy",
        "service": f"{settings.APP_NAME} Worker",
        "version": settings.APP_VERSION,
        "environment": "DEBUG" if settings.DEBUG else "PRODUCTION",
    }
