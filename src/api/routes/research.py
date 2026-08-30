"""Research API routes — thin HTTP adapters delegating to ResearchHandler."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from src.shared.config import settings

from ..dependencies import get_current_user, get_research_handler
from ..handlers.research_handler import ResearchHandler
from ..schemas.research_schemas import (
    ResearchCancelResponse,
    ResearchFeedbackRequest,
    ResearchFeedbackResponse,
    ResearchInitiateRequest,
    ResearchInitiateResponse,
    ResearchJobListItem,
    ResearchResultResponse,
    ResearchStatusResponse,
)

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/research",
    tags=["research"],
)

ResearchHandlerDep = Annotated[ResearchHandler, Depends(get_research_handler)]


@router.post(
    "/initiate",
    response_model=ResearchInitiateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def initiate_research(
    request: ResearchInitiateRequest,
    background_tasks: BackgroundTasks,
    handler: ResearchHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Trigger an asynchronous research swarm for the given company."""
    return await handler.initiate_research(request, background_tasks, current_user)


@router.get(
    "/jobs",
    response_model=list[ResearchJobListItem],
    response_model_exclude_none=True,
)
async def list_research_jobs(
    handler: ResearchHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """List the current user's research runs from the last 7 days."""
    return handler.list_jobs(
        user_email=current_user["email"], limit=limit, offset=offset
    )


@router.get(
    "/status/{job_id}",
    response_model=ResearchStatusResponse,
    response_model_exclude_none=True,
)
async def get_research_status(
    job_id: str,
    handler: ResearchHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Poll the status and progress of a research job owned by the current user."""
    return handler.get_research_status(job_id, user_email=current_user["email"])


@router.get("/result/{job_id}", response_model=ResearchResultResponse)
async def get_research_result(
    job_id: str,
    handler: ResearchHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Retrieve the completed research report and model card for a job owned by current user."""
    return handler.get_research_result(job_id, user_email=current_user["email"])


@router.get("/download/{job_id}")
async def download_pdf_report(
    job_id: str,
    handler: ResearchHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Download the final research report as a PDF file for a job owned by current user."""
    return handler.download_pdf_report(job_id, user_email=current_user["email"])


@router.delete("/{job_id}", response_model=ResearchCancelResponse)
async def cancel_research_job(
    job_id: str,
    handler: ResearchHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Cancel an in-progress research job owned by the current user."""
    return handler.cancel_research(job_id, user_email=current_user["email"])


@router.post(
    "/{job_id}/feedback",
    response_model=ResearchFeedbackResponse,
    status_code=status.HTTP_200_OK,
)
async def submit_feedback(
    job_id: str,
    request: ResearchFeedbackRequest,
    handler: ResearchHandlerDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """Submit feedback for a completed research job."""
    return await handler.submit_feedback(
        job_id, request, user_email=current_user["email"]
    )
