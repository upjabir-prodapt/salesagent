"""ADK session service factory."""

from google.adk.sessions import InMemorySessionService

from ....core.logging_config import logger


def build_session_service() -> InMemorySessionService:
    logger.info("ADK session service: InMemorySessionService (ephemeral per job run)")
    return InMemorySessionService()
