"""FastAPI service dependencies and auth dependencies for the API role."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from src.api.core.apigee_auth import get_authenticated_user
from src.api.core.security import AuthenticatedUser

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
#
# Apigee is the sole authority for authentication and authorization; all
# verification actually happens in apigee_auth.get_authenticated_user. These
# names are kept so downstream route code needs zero changes.


async def verify_token(
    user: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> AuthenticatedUser:
    """Backward-compatible name: delegates to Apigee-based authentication."""
    return user


async def get_current_user(
    user: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> dict[str, Any]:
    """Backward-compatible dependency returning a dict-shaped user context."""
    return {
        "oid": user.oid,
        "email": user.email,
        "roles": user.roles,
        "business_unit": user.business_unit,
        "organization": user.organization,
    }


async def get_current_user_context(
    request: Request,
    user: Annotated[AuthenticatedUser, Depends(get_authenticated_user)],
) -> AuthenticatedUser:
    """FastAPI dependency to extract normalized user context from Apigee headers."""
    request.state.user = user
    return user
