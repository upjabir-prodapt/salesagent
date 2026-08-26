"""GCS artifact uploads for research jobs."""

from __future__ import annotations

import asyncio

from src.shared.config import settings
from src.shared.logging_config import logger
from src.shared.repositories.bigquery_repository import BigQueryRepository
from src.shared.repositories.gcs_repository import GCSRepository
from src.shared.utils.tracing import job_attrs, traced


class ResearchArtifactService:
    """Upload final report, session state, and per-agent outputs to GCS."""

    def __init__(
        self,
        bigquery_repository: BigQueryRepository,
        gcs_repository: GCSRepository,
    ) -> None:
        self._bigquery_repo = bigquery_repository
        self._gcs_repo = gcs_repository

    @traced("research.artifacts.upload", attributes=job_attrs)
    def upload_artifacts(
        self, job_id: str, final_report: str, session_state: dict
    ) -> str:
        """Upload artifacts to GCS and update status."""
        logger.info(f"[Pipeline] Uploading artifacts to GCS for job_id={job_id}")
        self._bigquery_repo.update_status(
            job_id,
            "PROCESSING",
            progress=settings.RESEARCH_UPLOAD_PROGRESS,
            current_step=settings.RESEARCH_UPLOAD_STEP_LABEL,
        )
        self._gcs_repo.upload_json(job_id, session_state)
        return self._gcs_repo.upload_markdown(job_id, final_report)

    async def upload_agent_artifacts(
        self, job_id: str, session_state: dict
    ) -> dict[str, str]:
        """Upload each agent's output artifact to GCS."""
        agent_output_keys = [k for k in session_state if k.endswith("_output")]
        uris: dict[str, str] = {}
        for key in agent_output_keys:
            content = session_state.get(key)
            if not content:
                continue
            agent_name = key[: -len("_output")]
            try:
                payload = content if isinstance(content, str) else str(content)
                uri = await asyncio.to_thread(
                    self._gcs_repo.upload_agent_artifact,
                    job_id,
                    agent_name,
                    payload,
                )
                uris[key] = uri
            except Exception as e:
                logger.warning(
                    f"[Pipeline] Failed to upload artifact for agent={agent_name} "
                    f"job_id={job_id}: {e}"
                )
        if uris:
            logger.info(
                f"[Pipeline] Uploaded {len(uris)} per-agent artifacts for job_id={job_id}"
            )
        return uris
