"""Worker handler that runs the research pipeline for a Cloud Task."""

from __future__ import annotations

from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from src.shared.logging_config import logger
from src.shared.repositories.bigquery_repository import BigQueryRepository
from src.shared.repositories.gcs_repository import GCSRepository
from src.shared.schemas.tasks import ResearchTaskPayload

from ..services.pipeline_service import ResearchPipelineService

TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class ResearchTaskHandler:
    """Load research job state, honor idempotency, and run the research pipeline."""

    def __init__(
        self,
        pipeline_service: ResearchPipelineService | None = None,
        bigquery_repository: BigQueryRepository | None = None,
    ) -> None:
        self._bigquery = bigquery_repository or BigQueryRepository()
        self._pipeline = pipeline_service or ResearchPipelineService(
            bigquery_repository=self._bigquery,
            gcs_repository=GCSRepository(),
        )

    def _attach_trace(self, payload: ResearchTaskPayload) -> Any:
        """Continue API submit span via W3C traceparent when present."""
        if not payload.traceparent:
            return otel_context.get_current()
        carrier: dict[str, str] = {"traceparent": payload.traceparent}
        if payload.tracestate:
            carrier["tracestate"] = payload.tracestate
        return TraceContextTextMapPropagator().extract(carrier)

    async def handle(self, payload: ResearchTaskPayload) -> dict[str, Any]:
        """Process one research task.

        Returns a small status dict. Raises on transient failures so Cloud Tasks retries.
        Terminal no-ops return without raising so Cloud Tasks stops retrying.
        """
        job_id = payload.job_id
        parent_ctx = self._attach_trace(payload)

        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span(
            "worker.research_task",
            context=parent_ctx,
            kind=SpanKind.CONSUMER,
            attributes={
                "research.job_id": job_id,
                "research.company_name": payload.company_name,
            },
        ) as span:
            job_status_data = self._bigquery.get_status(job_id)
            if not job_status_data:
                span.set_status(trace.Status(trace.StatusCode.ERROR, "job not found"))
                logger.error("Job %s not found in BigQuery", job_id)
                return {"job_id": job_id, "status": "not_found", "action": "noop"}

            status = str(job_status_data.get("status") or "").upper()
            if status in TERMINAL_STATUSES:
                logger.info("Job %s already terminal (%s); skipping", job_id, status)
                return {"job_id": job_id, "status": status, "action": "noop"}

            trace_carrier: dict[str, str] = {}
            if payload.traceparent:
                trace_carrier["traceparent"] = payload.traceparent
            if payload.tracestate:
                trace_carrier["tracestate"] = payload.tracestate

            await self._pipeline.process_research_background(
                job_id,
                payload.company_name,
                metadata=payload.metadata,
                trace_context_headers=trace_carrier,
            )

            final_data = self._bigquery.get_status(job_id)
            final_status = str((final_data or {}).get("status") or "UNKNOWN")
            return {
                "job_id": job_id,
                "status": final_status,
                "action": "ran",
            }
