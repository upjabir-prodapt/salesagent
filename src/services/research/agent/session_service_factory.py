"""ADK session service factory.

ADK session state (company_name, sub-agent outputs, final_report) is ephemeral
and only needed for the duration of a single Runner.run_async() call. After the
run completes the final report is extracted from session.state and persisted to
GCS and BigQuery. InMemorySessionService is therefore the correct choice — no
database required.
"""

from google.adk.sessions import InMemorySessionService

from ....core.logging_config import logger


def build_session_service() -> InMemorySessionService:
    logger.info("ADK session service: InMemorySessionService (ephemeral per job run)")
    return InMemorySessionService()
