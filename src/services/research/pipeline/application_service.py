"""Application service facade over the research job orchestrator."""

from __future__ import annotations

from opentelemetry.trace import Span

from .commands import ResearchJobCommand
from .orchestrator import ResearchJobOrchestrator


class ResearchApplicationService:
    """Use-case level service to execute background research commands."""

    def __init__(self, orchestrator: ResearchJobOrchestrator) -> None:
        self._orchestrator = orchestrator

    async def run_background_job(
        self, command: ResearchJobCommand, *, span: Span | None = None
    ) -> None:
        await self._orchestrator.run(
            command.job_id,
            command.company_name,
            span=span,
        )
