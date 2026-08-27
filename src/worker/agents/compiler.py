"""ReportCompiler: compiles SearchFindings + ColtAlignment into the final
Strategic Account Brief markdown report.

Per the user's design requirement: this step's input is exactly
CompilerInput (findings + alignment) -- no query plan, no raw session
context. Output validation (formerly the validate_final_report ADK tool
with PlanReAct FINAL_ANSWER tags) is now a plain in-process call to the
existing OutputGuardrail after the model responds -- fixes the dependency
on PlanReAct tags that no planner emits anymore.

Fixes bug C2: the old REPORT_COMPILER_PROMPT used {{var}} (double-brace)
placeholders requiring a hand-rolled regex renderer in callbacks/model.py.
This version renders the prompt directly from the typed CompilerInput via
plain str.format(), with prompt *text* kept equivalent to the original.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent
from google.adk.models import Gemini

from src.shared.config import settings
from src.shared.utils.guardrails import OutputGuardrail
from src.worker.agents.base import AdkAgentStep, InvalidOutputError, RetryPolicy
from src.worker.agents.models import CompilerInput, Report
from src.worker.agents.safety import get_safety_config_for_agent
from src.worker.model import retry_config
from src.worker.services.formatting import clean_markdown_report

_REPORT_COMPILER_PROMPT_TEMPLATE = """You are the Report Compiler for Colt Technology Services. Your job is to compile the research findings and Colt alignment analysis into a professional, executive-ready "Strategic Account Brief" formatted in clean GitHub-Flavored Markdown.

---

## Target Account Inputs

**Company:** {company}

{domain_findings_block}

**Colt Alignment Mappings:**
{alignment_mappings_block}

**Strategic Opportunity Summary:** {opportunity_summary}
**Opening Hooks:** {hooks_block}

---

## Required Structure (13 Sections)

Your output must follow this exact section structure:

# Strategic Account Brief: {company}

## Company Snapshot
## 1. Company Overview
## 2. Key Executive Bios
## 3. Strategic Priorities and Business Goals (Next 2-5 Years)
## 4. Current Market Position & Outlook
## 5. Technology Landscape
## 6. Key Business & IT Challenges
## 7. Procurement & Technology Buying Patterns
## 8. Colt Technology Alignment Table
## 9. Relationship Landscape & Potential Synergies
## 10. Regional Spend & Infrastructure Overlay
## 11. Strategic Opportunity & Live Call Readiness
## 12. Signals
## 13. Source Summary

Output ONLY the markdown report body -- no commentary, no code fences.
"""


def _render_domain_findings_block(findings) -> str:  # noqa: ANN001
    lines: list[str] = []
    for output_key, finding in findings.domains.items():
        if finding.content.strip():
            lines.append(f"**{output_key}:** {finding.content}")
    return "\n".join(lines) if lines else "(no domain findings available)"


def _render_alignment_mappings_block(alignment) -> str:  # noqa: ANN001
    if not alignment.mappings:
        return "(no alignment mappings available)"
    return "\n".join(
        f"- Challenge: {m.challenge} | Colt Solution: {m.solution} | Why: {m.justification}"
        for m in alignment.mappings
    )


class ReportCompiler(AdkAgentStep[CompilerInput, Report]):
    """Compiles the final markdown report from exactly findings + alignment."""

    name = "ReportCompiler"

    def __init__(
        self, *, model: str | None = None, retry: RetryPolicy | None = None
    ) -> None:
        self._model = model or settings.GEMINI_MODEL
        self._guardrail = OutputGuardrail()
        if retry is not None:
            self.retry = retry

    def build_agent(self) -> LlmAgent:
        return LlmAgent(
            name=self.name,
            model=Gemini(model=self._model, retry_options=retry_config),
            instruction="You are the Report Compiler for Colt Technology Services.",
            tools=[],
            output_key="final_report",
            include_contents="none",
            description="Compiles the final markdown report from findings and alignment.",
            generate_content_config=get_safety_config_for_agent(self.name),
        )

    def to_input(self, request: CompilerInput) -> str:
        alignment = request.alignment
        return _REPORT_COMPILER_PROMPT_TEMPLATE.format(
            company=request.company,
            domain_findings_block=_render_domain_findings_block(request.findings),
            alignment_mappings_block=_render_alignment_mappings_block(alignment),
            opportunity_summary=alignment.opportunity_summary,
            hooks_block=", ".join(alignment.hooks) or "(none)",
        )

    def to_output(self, raw: Any, usage: tuple[int, int]) -> Report:
        markdown = clean_markdown_report(str(raw))
        return Report(markdown=markdown, validation_status="PENDING")

    async def execute(self, request: CompilerInput) -> Report:
        report = await super().execute(request)
        result = await self._guardrail.validate(report.markdown)
        violations = tuple(
            {"rule": v.rule, "detail": v.detail} for v in result.violations
        )
        status = "PASSED" if result.is_valid else "FAILED"
        # validate() (called by Agent.run() right after this returns) will
        # raise InvalidOutputError on FAILED, triggering a step-level retry.
        return Report(
            markdown=report.markdown,
            validation_status=status,
            validation_violations=violations,
        )

    def validate(self, result: Report) -> None:
        if result.validation_status == "FAILED":
            details = "; ".join(
                f"{v['rule']}: {v['detail']}" for v in result.validation_violations[:5]
            )
            raise InvalidOutputError(
                f"{self.name}: report failed output validation: {details}",
                agent_name=self.name,
            )


__all__ = ["ReportCompiler"]
