"""Research API handlers."""

from __future__ import annotations

import io
import re
from typing import Any

from fastapi import BackgroundTasks
from fastapi.responses import StreamingResponse
from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from ..core.config import settings
from ..core.exceptions import ResourceNotFoundError, ServiceError
from ..core.logging_config import contextualize, logger
from ..models.research_schemas import (
    ModelCard,
    ResearchInitiateRequest,
    ResearchInitiateResponse,
    ResearchResultResponse,
    ResearchStatusResponse,
)
from ..services.research import ResearchService
from ..utils.guardrails import InputGuardrail


class ResearchHandler:
    """Maps research HTTP operations to ResearchService."""

    def __init__(self, service: ResearchService) -> None:
        self._service = service

    async def initiate_research(
        self,
        request: ResearchInitiateRequest,
        background_tasks: BackgroundTasks,
        current_user: dict[str, Any],
    ) -> ResearchInitiateResponse:
        with contextualize(
            user_email=current_user["email"],
            business_unit=current_user["business_unit"],
            organization=current_user["organization"],
        ):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span("research.request.accepted") as span:
                span.set_attribute("research.company_name", request.company_name)
                span.set_attribute("research.account_id", request.account_id)
                InputGuardrail().validate(
                    request.company_name, field_name="company_name"
                )

                metadata = {
                    "account_id": request.account_id,
                    "user_id": current_user["email"],
                    "username": current_user["email"].split("@")[0],
                    "business_unit": current_user["business_unit"],
                    "organization": current_user["organization"],
                }
                trace_carrier: dict[str, str] = {}
                TraceContextTextMapPropagator().inject(trace_carrier)

                job_id = self._service.new_job_id()
                span.set_attribute("research.job_id", job_id)

                success = self._service.create_research_request(
                    job_id=job_id,
                    company_name=request.company_name,
                    metadata=metadata,
                )
                if not success:
                    span.set_attribute("research.db_write_success", False)
                    raise ServiceError("Failed to create job in database")
                span.set_attribute("research.db_write_success", True)

                background_tasks.add_task(
                    self._service.process_research_background,
                    job_id,
                    request.company_name,
                    metadata=metadata,
                    trace_context_headers=trace_carrier,
                )
                span.set_attribute("research.background_enqueued", True)

                logger.info(
                    "Initiated research job %s for company '%s' (account=%s, user=%s)",
                    job_id,
                    request.company_name,
                    request.account_id,
                    current_user["email"],
                )

                return ResearchInitiateResponse(
                    job_id=job_id,
                    status="PENDING",
                    check_status_url=f"{settings.API_PREFIX}/research/status/{job_id}",
                )

    def get_research_status(self, job_id: str) -> ResearchStatusResponse:
        status_data = self._service.get_request_status(job_id)
        if not status_data:
            raise ResourceNotFoundError(f"Job {job_id} not found")
        return ResearchStatusResponse(
            request_id=status_data["request_id"],
            status=status_data["status"],
            progress=status_data.get("progress", 0),
            current_step=status_data.get("current_step"),
            current_agent=status_data.get("current_agent"),
        )

    def get_research_result(self, job_id: str) -> ResearchResultResponse:
        result = self._service.get_request_result(job_id)
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
            request_id=str(result.get("request_id", "")),
            status=str(result.get("status", "")),
            report_content=result.get("report_content"),
            download_url=result.get("download_url"),
            model_card=model_card,
        )

    def download_pdf_report(self, job_id: str) -> StreamingResponse:
        result = self._service.get_pdf_report(job_id)
        if result is None:
            raise ResourceNotFoundError(f"Job {job_id} not found")

        pdf_bytes, company_name = result
        filename = _sanitize_filename(company_name)
        logger.info("Serving PDF download for job %s: %s", job_id, filename)

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


def _sanitize_filename(name: str) -> str:
    safe_name = re.sub(r"[^\w\s-]", "", name).strip()
    safe_name = re.sub(r"\s+", "_", safe_name)
    return f"Research_Report_{safe_name}.pdf"
