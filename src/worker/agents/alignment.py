"""AlignmentAnalyst: maps a company's search findings onto Colt's product
catalog and produces a strategic sales opportunity brief.

Fixes bug C5: the old design gave the agent both output_schema and a
retrieve_alignment_context() tool, forcing a two-turn flow that
include_contents="none" made unreliable. The catalog text is fetched once
here and injected directly into the rendered prompt at request time --
no tool call, no extra LLM round-trip.

Per the user's design requirement: this step consumes exactly
SearchFindings (from SearchExecutor) and nothing else -- no raw session
context, no query plan.
"""

from __future__ import annotations

from typing import Any

from google.adk.agents import LlmAgent
from pydantic import BaseModel, Field

from src.shared.config import settings
from src.worker.agents.base import AdkAgentStep, RetryPolicy
from src.worker.agents.models import ColtAlignment, ColtAlignmentMapping, SearchFindings
from src.worker.agents.safety import get_safety_config_for_agent
from src.worker.agents.tools.gcs_pdf_loader import get_alignment_context
from src.worker.model import RegionalGemini, retry_config

# NOTE on Gemini explicit context caching (2026-08-30): a Gemini
# cached_content reference cannot be combined with a request that also
# sets system_instruction/tools ("Tool config, tools and system
# instruction should not be set in the request when using cached
# content." -- confirmed live). ADK's LlmAgent unconditionally injects a
# system_instruction identity block for every root agent
# (flows/llm_flows/identity.py: `if agent.mode != 'single_turn'`), and
# `mode='single_turn'` is rejected outright for a root agent under
# Runner ("LlmAgent as root agent must have mode='chat'", confirmed
# live). There is therefore no supported way to use cached_content with
# an AdkAgentStep-driven root LlmAgent in this ADK version. Caching was
# implemented and then reverted here after live testing surfaced this
# incompatibility; see git history for the attempted implementation if
# ADK adds a way to suppress the identity injection in a future version.


class _ColtAlignmentMappingSchema(BaseModel):
    challenge_or_priority: str = Field(
        ..., description="Specific target company challenge or priority"
    )
    colt_solution: str = Field(
        ..., description="Colt solution enabler that addresses this challenge"
    )
    alignment_justification: str = Field(
        ..., description="Commercial pitch and value proposition"
    )


class _StrategicOpportunitySchema(BaseModel):
    summary: str = Field(description="Executive-level Why Colt? Why Now?")
    hooks: list[str] = Field(default_factory=list)
    executive_narratives: list[str] = Field(default_factory=list)
    regulatory_triggers: list[str] = Field(default_factory=list)
    ai_urgency: list[str] = Field(default_factory=list)
    competitive_displacement_angles: list[str] = Field(default_factory=list)
    colt_differentiation: list[str] = Field(default_factory=list)


class ColtAlignmentOutputSchema(BaseModel):
    """ADK output_schema for AlignmentAnalyst."""

    alignment_mappings: list[_ColtAlignmentMappingSchema] = Field(...)
    strategic_opportunity: _StrategicOpportunitySchema = Field(...)


_ALIGNMENT_PROMPT_TEMPLATE = """You are the Colt Alignment Analyst. You map a target company's researched enterprise challenges to Colt Technology Services' solutions and commercial opportunities.

You produce two deliverables in a single structured response matching `ColtAlignmentOutputSchema`:
1. `alignment_mappings`: Challenge-to-solution table (target need -> Colt offering -> why it fits).
2. `strategic_opportunity`: Executive sales narrative ("Why Colt? Why Now?", hooks, urgency, differentiation).

---

## Target Account (from prior research findings only)

**Company:** {company}

Read these researched domain findings. They are your ONLY source for facts about the target account:

{domain_findings_block}

---

## Colt Portfolio Knowledge

The following is authoritative context on Colt Technology Services (portfolio, NaaS, cloud on-ramps, SD-WAN/SASE, low-latency financial trading routes, certifications, SLAs, and differentiation). Use it as your ONLY source for Colt product/SLA claims:

{colt_catalog_context}

## Required Task

Synthesise the target account findings and the Colt catalog above into one complete `ColtAlignmentOutputSchema` (`alignment_mappings` + `strategic_opportunity`).

## Anti-Hallucination Rules

- **No training data** for target-account facts; use the domain findings above only.
- **No training data** for Colt product/SLA claims; use the Colt catalog context above only.
- **No fabricated urgency** -- no invented fines, AI programs, or campaigns absent from the domain findings.
- **Mandatory source tagging** on strategic opportunity claims: `[Source: <domain> -- "<exact data point>"]`.
- Do not use Colt product details as evidence for target-company behaviour.
"""


def _render_domain_findings_block(findings: SearchFindings) -> str:
    lines: list[str] = []
    for output_key, finding in findings.domains.items():
        if finding.content.strip():
            lines.append(f"**{output_key}:** {finding.content}")
    return "\n\n".join(lines) if lines else "(no domain findings available)"


class AlignmentAnalyst(AdkAgentStep[SearchFindings, ColtAlignment]):
    """Maps SearchFindings to Colt's catalog. No tool call, no other input."""

    name = "AlignmentAnalyst"

    def __init__(
        self, *, model: str | None = None, retry: RetryPolicy | None = None
    ) -> None:
        self._model = model or settings.GEMINI_MODEL
        if retry is not None:
            self.retry = retry

    def build_agent(self) -> LlmAgent:
        return LlmAgent(
            name=self.name,
            model=RegionalGemini(model=self._model, retry_options=retry_config),
            instruction="You are the Colt Alignment Analyst.",
            tools=[],
            output_key="alignment_output",
            output_schema=ColtAlignmentOutputSchema,
            include_contents="none",
            description="Maps company challenges to Colt solutions using catalog context.",
            generate_content_config=get_safety_config_for_agent(self.name),
        )

    def to_input(self, request: SearchFindings) -> str:
        catalog_context = get_alignment_context(request.company)
        return _ALIGNMENT_PROMPT_TEMPLATE.format(
            company=request.company,
            domain_findings_block=_render_domain_findings_block(request),
            colt_catalog_context=catalog_context,
        )

    def to_output(self, raw: Any, usage: tuple[int, int]) -> ColtAlignment:
        if isinstance(raw, str):
            schema = ColtAlignmentOutputSchema.model_validate_json(raw)
        elif isinstance(raw, dict):
            schema = ColtAlignmentOutputSchema.model_validate(raw)
        else:
            schema = raw

        mappings = tuple(
            ColtAlignmentMapping(
                challenge=m.challenge_or_priority,
                solution=m.colt_solution,
                justification=m.alignment_justification,
            )
            for m in schema.alignment_mappings
        )
        opp = schema.strategic_opportunity
        return ColtAlignment(
            mappings=mappings,
            opportunity_summary=opp.summary,
            hooks=tuple(opp.hooks),
            executive_narratives=tuple(opp.executive_narratives),
            regulatory_triggers=tuple(opp.regulatory_triggers),
            ai_urgency=tuple(opp.ai_urgency),
            competitive_displacement_angles=tuple(opp.competitive_displacement_angles),
            colt_differentiation=tuple(opp.colt_differentiation),
        )


__all__ = ["AlignmentAnalyst", "ColtAlignmentOutputSchema"]
