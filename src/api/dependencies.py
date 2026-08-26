"""FastAPI service dependencies and auth dependencies for the API role."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Security, status

from src.api.core.security import (
    SESSION_COOKIE_NAME,
    AuthenticatedUser,
    app_auth_scheme,
    decode_and_verify_token,
    extract_bearer_token,
)

from ..shared.repositories.bigquery_repository import BigQueryRepository
from ..shared.repositories.gcs_repository import GCSRepository
from .handlers.research_handler import ResearchHandler
from .services.cloud_tasks_service import CloudTasksService
from .services.research_job_service import ResearchJobService

_bq_repo: BigQueryRepository | None = None
_gcs_repo: GCSRepository | None = None


def get_bigquery_repository() -> BigQueryRepository:
    """Get shared BigQuery repository instance."""
    global _bq_repo
    if _bq_repo is None:
        _bq_repo = BigQueryRepository()
    return _bq_repo


def get_gcs_repository() -> GCSRepository:
    """Get shared GCS repository instance."""
    global _gcs_repo
    if _gcs_repo is None:
        _gcs_repo = GCSRepository()
    return _gcs_repo


def get_research_job_service() -> ResearchJobService:
    """Get ResearchJobService instance for API operations."""
    return ResearchJobService(
        bigquery_repository=get_bigquery_repository(),
        gcs_repository=get_gcs_repository(),
    )


def get_cloud_tasks_service() -> CloudTasksService:
    """Get Cloud Tasks enqueue service instance."""
    return CloudTasksService()


def get_research_handler() -> ResearchHandler:
    """Get research request handler instance."""
    return ResearchHandler(
        service=get_research_job_service(),
        cloud_tasks_service=get_cloud_tasks_service(),
    )


# --- Auth Dependencies --------------------------------------------------------


async def verify_token(
    request: Request,
    api_key: Annotated[str | None, Security(app_auth_scheme)],
) -> dict[str, Any]:
    """Verify bearer token from x-app-auth or the session cookie."""
    token = extract_bearer_token(api_key) or extract_bearer_token(
        request.cookies.get(SESSION_COOKIE_NAME)
    )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Not authenticated. Set x-app-auth to the access_token from "
                "POST /api/v1/auth/token, or Bearer <access_token>."
            ),
            headers={"WWW-Authenticate": "Bearer"},
        )
    return decode_and_verify_token(token)


async def get_current_user(
    payload: Annotated[dict[str, Any], Depends(verify_token)],
) -> dict[str, Any]:
    """Backward-compatible dependency returning raw token payload with email mapping."""
    if "sub" in payload and "email" not in payload:
        payload["email"] = payload["sub"]
    return payload


async def get_current_user_context(
    request: Request,
    payload: Annotated[dict[str, Any], Depends(verify_token)],
) -> AuthenticatedUser:
    """FastAPI dependency to extract normalized user context from JWT."""
    user = AuthenticatedUser(
        email=str(payload["sub"]),
        business_unit=str(payload["business_unit"]),
        organization=str(payload["organization"]),
    )
    request.state.user = user
    return user
