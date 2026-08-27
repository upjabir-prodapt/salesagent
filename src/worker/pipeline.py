"""ResearchPipeline: explicit composition of the 4 research steps.

Replaces src/worker/agents/workflow.py (SalesResearchWorkflowAgent root
agent) + src/worker/services/orchestrator.py (ResearchJobOrchestrator +
4 pass-through adapter classes) + src/worker/runtime/runner.py (ADK
multi-agent Runner lifecycle wrapper).

Per the user's design requirement: no shared session/context between
steps. Each step receives exactly the typed output(s) of its
predecessor(s) -- QueryPlanner's output feeds SearchExecutor, whose
output feeds AlignmentAnalyst, and ReportCompiler receives exactly
SearchFindings + ColtAlignment via CompilerInput. Retry is handled
entirely inside each step's own Agent.run() loop; this class does not
implement any retry logic itself.
"""

from __future__ import annotations

from src.worker.agents.alignment import AlignmentAnalyst
from src.worker.agents.compiler import ReportCompiler
from src.worker.agents.models import (
    CompilerInput,
    PipelineResult,
    ResearchRequest,
)
from src.worker.agents.planner import QueryPlanner
from src.worker.agents.search import SearchExecutor
from src.worker.observers import CompositeObserver, Observer, TelemetryObserver


class ResearchPipeline:
    """Runs the 4-step research pipeline for one company end to end."""

    def __init__(
        self,
        planner: QueryPlanner,
        searcher: SearchExecutor,
        analyst: AlignmentAnalyst,
        compiler: ReportCompiler,
    ) -> None:
        self._planner = planner
        self._searcher = searcher
        self._analyst = analyst
        self._compiler = compiler

    async def run(self, request: ResearchRequest, observer: Observer) -> PipelineResult:
        telemetry = TelemetryObserver(request.job_id)
        obs = CompositeObserver([observer, telemetry])

        plan = await self._planner.run(request, obs)
        findings = await self._searcher.run(plan, obs)
        alignment = await self._analyst.run(findings, obs)
        report = await self._compiler.run(
            CompilerInput(
                company=request.company, findings=findings, alignment=alignment
            ),
            obs,
        )

        return PipelineResult(
            report=report,
            findings=findings,
            alignment=alignment,
            telemetry_records=telemetry.records(),
            token_usage_by_model=telemetry.token_usage(),
        )


__all__ = ["ResearchPipeline"]
