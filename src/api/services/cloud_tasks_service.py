"""Cloud Tasks enqueue client for the public Research API."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import requests
from google.api_core import exceptions as gcp_exceptions
from google.cloud import tasks_v2
from google.protobuf import duration_pb2

from src.shared.config import settings
from src.shared.schemas.tasks import ResearchTaskPayload

logger = logging.getLogger(__name__)

_TASK_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


class CloudTasksService:
    """Enqueue research jobs onto a Cloud Tasks HTTP queue."""

    def __init__(self, client: tasks_v2.CloudTasksClient | None = None):
        self._client = client

    @property
    def client(self) -> tasks_v2.CloudTasksClient:
        if self._client is None:
            self._client = tasks_v2.CloudTasksClient()
        return self._client

    def _queue_path(self) -> str:
        project = settings.CLOUD_TASKS_PROJECT or settings.GOOGLE_CLOUD_PROJECT
        return self.client.queue_path(
            project,
            settings.CLOUD_TASKS_LOCATION,
            settings.CLOUD_TASKS_QUEUE,
        )

    @staticmethod
    def task_id_for_job(job_id: str) -> str:
        """Derive a Cloud Tasks task id from job_id (idempotent create)."""
        safe = _TASK_NAME_SAFE.sub("-", job_id)
        return f"research-{safe}"[:500]

    def _is_local_http_target(self) -> bool:
        """Return True when the configured worker URL is a local HTTP target.

        In that case we bypass Cloud Tasks and POST directly to the worker so
        local development does not require an HTTPS endpoint or OIDC token.
        """
        url = settings.CLOUD_TASKS_WORKER_URL
        return settings.IS_LOCAL and url.startswith("http://")

    def _enqueue_local_http(
        self,
        job_id: str,
        company_name: str,
        metadata: dict[str, Any],
        *,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> str:
        """Direct HTTP dispatch for local development."""
        logger.info(
            "Local dispatch for research job %s company=%s",
            job_id,
            company_name,
        )
        payload = ResearchTaskPayload(
            job_id=job_id,
            company_name=company_name,
            metadata=metadata,
            traceparent=traceparent,
            tracestate=tracestate,
        )
        body = json.dumps(payload.model_dump(exclude_none=True)).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if traceparent:
            headers["traceparent"] = traceparent
        if tracestate:
            headers["tracestate"] = tracestate

        url = settings.CLOUD_TASKS_WORKER_URL
        response = requests.post(url, data=body, headers=headers, timeout=3600.0)
        response.raise_for_status()
        logger.info(
            "Dispatched research job %s directly to local worker at %s", job_id, url
        )
        return f"local-http/{job_id}"

    def enqueue_research(
        self,
        job_id: str,
        company_name: str,
        metadata: dict[str, Any] | None = None,
        *,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> str:
        """Create an HTTP task targeting the worker. Returns the task name.

        Raises on failure so the caller can compensate (mark job failed).
        """
        if not settings.CLOUD_TASKS_WORKER_URL:
            raise RuntimeError("CLOUD_TASKS_WORKER_URL is not configured")

        meta = metadata or {}

        if self._is_local_http_target():
            return self._enqueue_local_http(
                job_id,
                company_name,
                meta,
                traceparent=traceparent,
                tracestate=tracestate,
            )

        if not settings.CLOUD_TASKS_QUEUE:
            raise RuntimeError("CLOUD_TASKS_QUEUE is not configured")
        if not settings.CLOUD_TASKS_OIDC_SERVICE_ACCOUNT:
            raise RuntimeError("CLOUD_TASKS_OIDC_SERVICE_ACCOUNT is not configured")

        payload = ResearchTaskPayload(
            job_id=job_id,
            company_name=company_name,
            metadata=meta,
            traceparent=traceparent,
            tracestate=tracestate,
        )
        body = json.dumps(payload.model_dump(exclude_none=True)).encode("utf-8")

        task: dict[str, Any] = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": settings.CLOUD_TASKS_WORKER_URL,
                "headers": {"Content-Type": "application/json"},
                "body": body,
                "oidc_token": {
                    "service_account_email": settings.CLOUD_TASKS_OIDC_SERVICE_ACCOUNT,
                    "audience": settings.WORKER_OIDC_AUDIENCE
                    or settings.CLOUD_TASKS_WORKER_URL,
                },
            }
        }

        deadline = int(settings.CLOUD_TASKS_DISPATCH_DEADLINE_SECONDS)
        if deadline > 0:
            task["dispatch_deadline"] = duration_pb2.Duration(seconds=deadline)

        parent = self._queue_path()
        task_name = f"{parent}/tasks/{self.task_id_for_job(job_id)}"
        task["name"] = task_name

        try:
            created = self.client.create_task(request={"parent": parent, "task": task})
            logger.info(
                "Enqueued Cloud Task %s for job %s queue=%s",
                created.name,
                job_id,
                settings.CLOUD_TASKS_QUEUE,
            )
            return created.name
        except gcp_exceptions.AlreadyExists:
            logger.info(
                "Cloud Task already exists for job %s (%s); treating as success",
                job_id,
                task_name,
            )
            return task_name
