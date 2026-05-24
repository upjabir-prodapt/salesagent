"""BigQuery progress milestones during ADK agent runs."""

from __future__ import annotations

import time
from typing import Any

from ...core.config import settings
from ...core.logging_config import logger
from ...repositories.bigquery_repository import BigQueryRepository
from ...utils.guardrails import AgentGuardrail


class ResearchProgressTracker:
    """Debounced status writes and guardrail checks on ADK events."""

    def __init__(self, bigquery_repository: BigQueryRepository) -> None:
        self._bigquery_repo = bigquery_repository

    def process_event_milestones(
        self,
        event: Any,
        job_id: str,
        total_agents: int,
        completed_agents: set[str],
        agent_descriptions: dict[str, str],
        status_write_state: dict[str, Any],
    ) -> None:
        """Handle individual agent events and BQ progress updates."""
        if not hasattr(event, "author") or not event.author:
            return

        author = event.author
        is_final = getattr(event, "is_final_response", lambda: False)()

        seen_authors: set[str] = status_write_state.setdefault("seen_authors", set())
        if (
            author not in seen_authors
            and author not in completed_agents
            and author not in ("user",)
        ):
            seen_authors.add(author)
            try:
                self._bigquery_repo.update_status(
                    job_id,
                    None,
                    current_step=f"Running: {author}",
                    metadata_update={"current_agent": author},
                )
            except Exception as e:
                logger.debug(f"Failed to write agent-start status for {author}: {e}")

        if is_final and hasattr(event, "response") and event.response:
            agent_text = getattr(event.response, "text", "")
            if agent_text:
                try:
                    AgentGuardrail().validate(agent_text, agent_name=author)
                except Exception as guard_err:
                    logger.error(
                        f"[AgentGuardrail] Violation in {author}: {guard_err}"
                    )
                    raise

        if is_final:
            completed_agents.add(author)
            pct = int((len(completed_agents) / total_agents) * 100)
            pct = min(pct, 99)

            description = agent_descriptions.get(author, "")
            label = (
                f"{author}: {description}"
                if description
                else f"{author} completed"
            )

            if self.should_write_status_update(
                status_write_state,
                progress=pct,
                current_step=label,
            ):
                self._bigquery_repo.update_status(
                    job_id,
                    None,
                    progress=pct,
                    current_step=label,
                )

    @staticmethod
    def should_write_status_update(
        status_write_state: dict[str, Any],
        *,
        progress: int | None,
        current_step: str | None,
    ) -> bool:
        """Debounce repeated status writes to BigQuery."""
        now = time.monotonic()
        same_payload = (
            status_write_state.get("last_progress") == progress
            and status_write_state.get("last_step") == current_step
        )
        min_interval = settings.RESEARCH_STATUS_MIN_UPDATE_INTERVAL_SECONDS
        recently_written = (
            now - float(status_write_state.get("last_write_ts", 0.0))
        ) < min_interval
        if same_payload and recently_written:
            return False
        status_write_state["last_progress"] = progress
        status_write_state["last_step"] = current_step
        status_write_state["last_write_ts"] = now
        return True
