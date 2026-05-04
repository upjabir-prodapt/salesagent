"""Research API Routes - Endpoints for research operations."""

import io
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import StreamingResponse
from loguru import logger

from ..agents.research_service import ResearchService
from ..core.config import settings
from ..core.exceptions import (
    ResourceNotFoundError,
    ServiceError,
)
from ..dependencies.service_dependencies import get_research_service
from ..models.common_schemas import ErrorResponse
from ..models.research_schemas import (
    ModelCard,
    ResearchInitiateRequest,
    ResearchInitiateResponse,
    ResearchResultResponse,
    ResearchStatusResponse,
)
from ..utils.guardrails import InputGuardrail

router = APIRouter(
    prefix=f"{settings.API_PREFIX}/research", 
    tags=["research"],
)

# Type alias for dependency injection (avoids B008 lint error)
ResearchServiceDep = Annotated[ResearchService, Depends(get_research_service)]


@router.post(
    "/initiate",
    response_model=ResearchInitiateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {
            "description": "Research job accepted for processing",
            "content": {
                "application/json": {
                    "example": {
                        "job_id": "job_123e4567-e89b-12d3-a456-426614174000",
                        "status": "PENDING",
                        "check_status_url": "/api/v1/research/status/job_123e4567-e89b-12d3-a456-426614174000",
                    }
                }
            },
        },
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Validation Error",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Internal Server Error",
        },
    },
)
async def initiate_research(
    request: ResearchInitiateRequest,
    background_tasks: BackgroundTasks,
    service: ResearchServiceDep,
):
    """
    Trigger an asynchronous research swarm for the given company.

    Returns immediately with a job_id while processing happens in the background.
    """
    # Use logger.contextualize to ensure all logs for this request include the identity
    with logger.contextualize(
        user_email=request.user_id,
        username=request.username,
        business_unit=request.business_unit,
        organization=request.organization
    ):
        # --- Input Guardrail: scan company_name for PII and jailbreak ---
        InputGuardrail().validate(request.company_name, field_name="company_name")

        job_id = f"{settings.JOB_ID_PREFIX}{uuid.uuid4()}"

        metadata = {
            "account_id": request.account_id,
            "user_id": request.user_id,
            "username": request.username,
            "business_unit": request.business_unit,
            "organization": request.organization,
        }

        success = await service.create_research_request(
            job_id=job_id,
            company_name=request.company_name,
            metadata=metadata,
        )

        if not success:
            raise ServiceError("Failed to create job in database")

        background_tasks.add_task(
            service.process_research_background,
            job_id,
            request.company_name,
            metadata=metadata,
        )

        logger.info(
            f"Initiated research job {job_id} for company '{request.company_name}' "
            f"(account={request.account_id}, user={request.user_id}, unit={request.business_unit})"
        )

        return ResearchInitiateResponse(
            job_id=job_id,
            status="PENDING",
            check_status_url=f"{settings.API_PREFIX}/research/status/{job_id}",
        )


@router.get(
    "/status/{job_id}",
    response_model=ResearchStatusResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Status retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "request_id": "job_123e4567-e89b-12d3-a456-426614174000",
                        "status": "PROCESSING",
                        "progress": 45,
                        "current_step": "Strategy Agent: Analyzing Annual Report",
                    }
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Job Not Found",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Internal Server Error",
        },
    },
)
async def get_research_status(job_id: str, service: ResearchServiceDep):
    """
    Poll the status and progress of a research job.
    """
    status_data = await service.get_request_status(job_id)

    if not status_data:
        raise ResourceNotFoundError(f"Job {job_id} not found")

    return ResearchStatusResponse(**status_data)


@router.get(
    "/result/{job_id}",
    response_model=ResearchResultResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Result retrieved successfully",
            "content": {
                "application/json": {
                    "example": {
                        "request_id": "job_123e4567-e89b-12d3-a456-426614174000",
                        "status": "COMPLETED",
                        "report_content": "# Sales Report for Acme Corp\n\n...",
                        "download_url": "https://storage.googleapis.com/...",
                        "model_card": {
                            "model_version": "gemini-2.5-pro",
                            "tokens_used": 28500,
                            "latency_seconds": 185.0,
                            "cost_usd": 0.35,
                        },
                    }
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Job Not Found",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Internal Server Error",
        },
    },
)
async def get_research_result(job_id: str, service: ResearchServiceDep):
    """
    Retrieve the completed research report and model card for a job.
    """
    result = await service.get_request_result(job_id)

    if not result:
        raise ResourceNotFoundError(f"Job {job_id} not found")

    model_card_data = result.get("model_card") or {}
    model_card = ModelCard(
        model_version=model_card_data.get("model_version"),
        tokens_used=model_card_data.get("tokens_used"),
        latency_seconds=model_card_data.get("latency_seconds"),
        cost_usd=model_card_data.get("cost_usd"),
    )

    return ResearchResultResponse(
        request_id=result.get("request_id"),
        status=result.get("status"),
        report_content=result.get("report_content"),
        download_url=result.get("download_url"),
        model_card=model_card,
    )


@router.get(
    "/download/{job_id}",
    responses={
        status.HTTP_200_OK: {
            "description": "PDF report download",
            "content": {"application/pdf": {}},
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Job not found",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Job not yet completed",
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {
            "model": ErrorResponse,
            "description": "Internal Server Error",
        },
    },
)
async def download_pdf_report(job_id: str, service: ResearchServiceDep):
    """
    Download the final research report as a PDF file.

    Returns the PDF as an attachment once the job is COMPLETED.
    Poll /status/{job_id} first to confirm the job is done.
    """
    result = await service.get_pdf_report(job_id)

    if result is None:
        raise ResourceNotFoundError(f"Job {job_id} not found")

    pdf_bytes, company_name = result

    # Build a safe filename: "Research_Report_<CompanyName>.pdf"
    safe_name = re.sub(r"[^\w\s-]", "", company_name).strip()
    safe_name = re.sub(r"\s+", "_", safe_name)
    filename = f"Research_Report_{safe_name}.pdf"

    logger.info(f"Serving PDF download for job {job_id}: {filename}")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
