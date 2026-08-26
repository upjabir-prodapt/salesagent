"""Internal Cloud Tasks routes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ...shared.schemas.tasks import ResearchTaskPayload
from ..dependencies import get_research_task_handler
from .auth import require_cloud_tasks_oidc
from .handlers import ResearchTaskHandler

logger = logging.getLogger(__name__)

router = APIRouter()

ResearchTaskHandlerDep = Annotated[
    ResearchTaskHandler, Depends(get_research_task_handler)
]


@router.post(
    "/internal/tasks/research",
    tags=["tasks"],
    dependencies=[Depends(require_cloud_tasks_oidc)],
)
async def research_task(
    request: Request,
    payload: ResearchTaskPayload,
    handler: ResearchTaskHandlerDep,
) -> dict[str, Any]:
    """Cloud Tasks target: run research pipeline for job_id."""
    logger.info(
        "Received research task request job_id=%s company=%s traceparent=%s",
        payload.job_id,
        payload.company_name,
        payload.traceparent,
    )
    try:
        result = await handler.handle(payload)
    except Exception as e:
        # Non-2xx causes Cloud Tasks to retry according to queue configuration
        logger.exception("Research task failed for job %s: %s", payload.job_id, e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline failed: {e}",
        ) from e

    logger.info("Research task completed for job %s: %s", payload.job_id, result)
    if result.get("status") == "not_found":
        # Permanent — return 200 so Tasks does not retry indefinitely
        return result

    return result
