"""Research API routes — thin HTTP adapters delegating to ResearchHandler."""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status

from ..core.config import settings
from ..dependencies.auth import get_current_user
from ..dependencies.handler_dependencies import get_research_handler
from ..handlers.research_handler import ResearchHandler
from ..models.research_schemas import (
    ResearchInitiateRequest,
    ResearchInitiateResponse,
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


@router.get("/status/{job_id}", response_model=ResearchStatusResponse)
async def get_research_status(job_id: str, handler: ResearchHandlerDep):
    """Poll the status and progress of a research job."""
    return handler.get_research_status(job_id)


@router.get("/result/{job_id}", response_model=ResearchResultResponse)
async def get_research_result(job_id: str, handler: ResearchHandlerDep):
    """Retrieve the completed research report and model card for a job."""
    return handler.get_research_result(job_id)


@router.get("/download/{job_id}")
async def download_pdf_report(job_id: str, handler: ResearchHandlerDep):
    """Download the final research report as a PDF file."""
    return handler.download_pdf_report(job_id)
