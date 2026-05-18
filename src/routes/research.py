"""Research API Routes - Endpoints for research operations."""

import io
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from ..agents.research_service import ResearchService
from ..core.config import settings
from ..core.exceptions import (
    ResourceNotFoundError,
    ServiceError,
)
from ..core.logging_config import contextualize, logger
from ..dependencies.auth import get_current_user
from ..dependencies.service_dependencies import get_research_service
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
)
async def initiate_research(
    request: ResearchInitiateRequest,
    background_tasks: BackgroundTasks,
    service: ResearchServiceDep,
    current_user: Annotated[dict, Depends(get_current_user)],
):
    """
    Trigger an asynchronous research swarm for the given company.

    Returns immediately with a job_id while processing happens in the background.
    """
    # Use contextualize to ensure all logs for this request include the identity
    with contextualize(
        user_email=current_user["email"],
        business_unit=current_user["business_unit"],
        organization=current_user["organization"],
    ):
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("research.request.accepted") as span:
            span.set_attribute("research.company_name", request.company_name)
            span.set_attribute("research.account_id", request.account_id)
            # --- Input Guardrail: scan company_name for PII and jailbreak ---
            InputGuardrail().validate(request.company_name, field_name="company_name")

            job_id = f"{settings.JOB_ID_PREFIX}{uuid.uuid4()}"
            span.set_attribute("research.job_id", job_id)

            metadata = {
                "account_id": request.account_id,
                "user_id": current_user["email"],
                "username": current_user["email"].split("@")[0],
                "business_unit": current_user["business_unit"],
                "organization": current_user["organization"],
            }
            trace_carrier: dict[str, str] = {}
            TraceContextTextMapPropagator().inject(trace_carrier)

            success = service.create_research_request(
                job_id=job_id,
                company_name=request.company_name,
                metadata=metadata,
            )

            if not success:
                span.set_attribute("research.db_write_success", False)
                raise ServiceError("Failed to create job in database")
            span.set_attribute("research.db_write_success", True)

            background_tasks.add_task(
                service.process_research_background,
                job_id,
                request.company_name,
                metadata=metadata,
                trace_context_headers=trace_carrier,
            )
            span.set_attribute("research.background_enqueued", True)

            logger.info(
                f"Initiated research job {job_id} for company '{request.company_name}' "
                f"(account={request.account_id}, user={current_user['email']}, unit={current_user['business_unit']})"
            )

            return ResearchInitiateResponse(
                job_id=job_id,
                status="PENDING",
                check_status_url=f"{settings.API_PREFIX}/research/status/{job_id}",
            )


@router.get(
    "/status/{job_id}",
    response_model=ResearchStatusResponse,
)
async def get_research_status(job_id: str, service: ResearchServiceDep):
    """
    Poll the status and progress of a research job.
    """
    status_data = service.get_request_status(job_id)

    if not status_data:
        raise ResourceNotFoundError(f"Job {job_id} not found")

    return ResearchStatusResponse(**status_data)


@router.get(
    "/result/{job_id}",
    response_model=ResearchResultResponse,
)
async def get_research_result(job_id: str, service: ResearchServiceDep):
    """
    Retrieve the completed research report and model card for a job.
    """
    result = service.get_request_result(job_id)

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


def _sanitize_filename(name: str) -> str:
    """Build a safe filename from a string."""
    safe_name = re.sub(r"[^\w\s-]", "", name).strip()
    safe_name = re.sub(r"\s+", "_", safe_name)
    return f"{safe_name}.pdf"


@router.get("/download/{job_id}")
async def download_pdf_report(job_id: str, service: ResearchServiceDep):
    """
    Download the final research report as a PDF file.

    Returns the PDF as an attachment once the job is COMPLETED.
    Poll /status/{job_id} first to confirm the job is done.
    """
    result = service.get_pdf_report(job_id)

    if result is None:
        raise ResourceNotFoundError(f"Job {job_id} not found")

    pdf_bytes, company_name = result

    # Build a safe filename: "<CompanyName>.pdf"
    filename = _sanitize_filename(company_name)

    logger.info(f"Serving PDF download for job {job_id}: {filename}")

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
