"""Worker handler that runs the research pipeline for a Cloud Task."""

from __future__ import annotations

from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from src.shared.logging_config import logger
from src.shared.repositories.bigquery_repository import BigQueryRepository
from src.shared.schemas.tasks import ResearchTaskPayload

from ..services.job_runner import ResearchJobRunner
from ..services.task_attempt import JobPhase, TaskAttempt, should_redispatch

TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


class ResearchTaskHandler:
    """Load research job state, honor idempotency, and run the research pipeline."""

    def __init__(
        self,
        job_runner: ResearchJobRunner,
        bigquery_repository: BigQueryRepository | None = None,
    ) -> None:
        self._bigquery = bigquery_repository or BigQueryRepository()
        self._job_runner = job_runner

    def _attach_trace(self, payload: ResearchTaskPayload) -> Any:
        """Continue API submit span via W3C traceparent when present."""
        if not payload.traceparent:
            return otel_context.get_current()
        carrier: dict[str, str] = {"traceparent": payload.traceparent}
        if payload.tracestate:
            carrier["tracestate"] = payload.tracestate
        return TraceContextTextMapPropagator().extract(carrier)

    async def handle(
        self,
        payload: ResearchTaskPayload,
        attempt: TaskAttempt | None = None,
    ) -> dict[str, Any]:
        """Process one research task.

        Returns a small status dict. Raises ONLY when Cloud Tasks should
        deliver the task again -- the route turns that into a 5XX, which
        is what triggers re-delivery. Every other outcome, including a
        permanent failure, returns normally so the queue stops.

        *attempt* is the delivery context from the Cloud Tasks headers
        (None for the local dev path). It is what makes the queue-level
        retry real: without it a failure was always written as FAILED, and
        the next delivery read that back and no-opped.
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

            try:
                await self._job_runner.run(
                    job_id,
                    payload.company_name,
                    metadata=payload.metadata,
                    span=span,
                    attempt=attempt,
                )
            except Exception as error:
                # The runner has already recorded the failure -- either as
                # FAILED, or (for a transient pipeline failure with a
                # delivery left) as still-PROCESSING. Re-raise only in the
                # latter case: a 5XX is the signal Cloud Tasks retries on,
                # and raising for a permanent failure just buys a wasted
                # re-delivery that the terminal-status guard above no-ops.
                if should_redispatch(error, attempt, JobPhase.PIPELINE):
                    span.set_status(
                        trace.Status(trace.StatusCode.ERROR, "retryable failure")
                    )
                    logger.warning(
                        "Job %s failed transiently (%s); asking Cloud Tasks to "
                        "re-deliver: %s",
                        job_id,
                        attempt.label if attempt else "no delivery context",
                        error,
                    )
                    raise
                span.set_status(trace.Status(trace.StatusCode.ERROR, "failed"))
                logger.error(
                    "Job %s failed permanently; not re-delivering: %s", job_id, error
                )
                return {"job_id": job_id, "status": "FAILED", "action": "failed"}

            final_data = self._bigquery.get_status(job_id)
            final_status = str((final_data or {}).get("status") or "UNKNOWN")
            return {
                "job_id": job_id,
                "status": final_status,
                "action": "ran",
            }
