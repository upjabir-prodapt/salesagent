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

import re
from dataclasses import dataclass
from typing import Any

from google.adk.agents import LlmAgent

from src.shared.config import settings
from src.shared.logging_config import logger
from src.shared.utils.guardrails import OutputGuardrail
from src.worker.agents.base import AdkAgentStep, InvalidOutputError, RetryPolicy
from src.worker.agents.models import CompilerInput, Report, SearchFindings
from src.worker.agents.safety import get_safety_config_for_agent
from src.worker.agents.tools.evidence import evidence_key
from src.worker.agents.tools.verification import Bm25Verifier
from src.worker.model import RegionalGemini, retry_config
from src.worker.services.formatting import clean_markdown_report

# Bm25Verifier.verify()/EvidenceStore.documents() were built against the
# old shared ADK session-state dict, keyed by agent name via
# evidence_key(agent_name) -- see tools/evidence.py and tools/
# verification.py. Rather than modify that module (still used as-is by
# evaluation/section_b.py's M6 metric), this constant supplies a stable
# agent_name so ReportCompiler can build a plain dict "state" from
# SearchFindings and reuse Bm25Verifier unchanged as a second, stronger
# gate on top of OutputGuardrail's structural checks.
_BM25_VERIFIER_AGENT_NAME = "ReportCompiler"


def _build_bm25_state(findings: SearchFindings) -> dict[str, Any]:
    """Build the minimal state dict Bm25Verifier/EvidenceStore need,
    populated from SearchFindings.all_evidence() (the real Google Search
    grounding citations captured by SearchExecutor -- see search.py).
    """
    entries = [
        {
            "url": e.url,
            "title": e.title,
            "snippet": e.snippet,
            "query": e.query,
            "agent": _BM25_VERIFIER_AGENT_NAME,
        }
        for e in findings.all_evidence()
    ]
    return {evidence_key(_BM25_VERIFIER_AGENT_NAME): entries}


_REPORT_COMPILER_PROMPT_TEMPLATE = """
You are the Report Compiler for  Colt Technology Services' sales research team . Your job is to compile the sales research findings and Colt alignment analysis into a professional, executive-ready "Sales playbook" formatted in Markdown.

---

## Target Account Inputs

**Company:** {company}

{domain_findings_block}

**Colt Alignment Mappings:**
{alignment_mappings_block}

**Strategic Opportunity Summary:** {opportunity_summary}
**Opening Hooks:** {hooks_block}

**Verified Source URLs (from Google Search grounding citations — this is the
authoritative, complete list; do not rely on scanning prose text above for
additional links):**
{evidence_urls_block}

---

Compile the FINAL Sales LEAD GENERATION REPORT following the exact template structure below.
- Work through the coverage checklist: no meaningful sourced field left unrepresented.
- Use bullets, sub-bullets, and tables freely for completeness (except §9 and §12 prose rules below).
- **CRITICAL — Section 13 (Source Summary):** Reproduce the **Verified Source URLs** list provided above verbatim, one URL per line, de-duplicated. Do **not** include agent names, output keys, or internal labels — URLs only. Do **not** invent URLs or attempt to re-extract links from prose.
- Use tables for `Operational Location Breakdown`, `Colt Alignment Table`, `Regional Spend`, and `Use Case Recommendations` when data supports them.

**EXACT SECTION STRUCTURE:**

## Company Snapshot
- Company Name, Sector, Global Revenue (most recent year), Previous Year Revenue, Employee Count, Estimated IT Spend, Company Short Summary (cover all snapshot fields from firmographics)

## 1. Company Overview
- Legal Name, HQ Location, Global Revenue, Employee Count, Business Model and Sector (detailed paragraph using all overview fields)
- Key Leadership: include **all** C-suite leaders present in `executiveagent_output` (CEO, CFO, COO, CIO, CTO, CISO, and other key roles)

### 1.1 Global Operations & Locations
- HQ country and key regional offices (North America, EMEA, APAC, LATAM)
- Primary manufacturing, data, R&D, or operational sites (**all** sites from geographic output)
- Key trading regions (every region with revenue or strategic data)
- Supply chain or partner dependencies
- Regional connectivity or infrastructure considerations
- **Operational Location Breakdown Table:** one row per site/region cluster where data exists
  | Region | Country | Approx. # of Sites / Offices | Key Cities / Sites | Operational Focus | Colt Network Presence |

## 2. Key Executive Bios
For **each** executive and key leader in `executiveagent_output` (C-suite, board, other key leaders):
- Full Name, Current Role & Start Date, Previous Roles (all available), Education, Public Statements / Strategic Influence (sourced quote)

## 3. Strategic Priorities and Business Goals (Next 2-5 Years)
- Include **all** transformation goals, digital plans, sustainability targets, M&A/expansion priorities, and leadership quotes from `strategyagent_output`

## 4. Current Market Position & Outlook
- Full revenue breakdown (geography, segment, product line — all rows/fields)
- Competitive landscape and market share (all competitors and share data found)
- Key market challenges and global trends (all items)
- Emerging markets or product/service focus areas (all items)

### 4.1 Strategic Partnerships & Ecosystem
- **All** technology, connectivity, and strategic partners; customer/alliance relationships; dependency insights from `ecosystemagent_output`

## 5. Technology Landscape
- **All** IT, cloud, network, cybersecurity, vendor, platform, AI/automation, and innovation fields from `techstackagent_output`

### 5.1 Regulatory, Compliance & Industry Standards
- **All** regulations, regulators, data sovereignty/privacy notes, certifications, frameworks, audits, and compliance issues from `complianceagent_output`

## 6. Key Business & IT Challenges
- Address **every** challenge category supported by `strategyagent_output`, with commercial impact for each
- Include operational complexity, cost pressures, hybrid work, cybersecurity, performance/latency, external pressures where evidenced

### 6.1 Financial & Trading Relevance
- **All** financial fields from `marketagent_output`: YoY growth, cost drivers, capex, supply chain exposure, customers, revenue distribution, procurement model, commercial leverage points

## 7. Procurement & Technology Buying Patterns
- **All** procurement fields from `procurementagent_output`

## 8. Colt Technology Alignment Table
| Business / IT Challenge or Priority | Colt Solution Enabler(s) | Alignment Justification |
Include **every** row from `alignment_output.alignment_mappings` — do not drop or merge rows to hit a target count.

## 9. Relationship Landscape & Potential Synergies
Write comprehensive prose paragraphs (NO bullet points or lists). Cover **all** ecosystem relationship content: shared customers/partners/bodies, Colt engagement history, ESG/DEI, co-innovation, strategic fit — every fact from `ecosystemagent_output` must appear here in flowing prose.

## 10. Regional Spend & Infrastructure Overlay
| Region | Revenue Contribution % | Key Sites / Offices | Estimated IT Spend | Colt Network Presence |
Include **all** regions with data from geographic and firmographics outputs.

## 11. Strategic Opportunity & Live Call Readiness
Transfer **all** content from `alignment_output.strategic_opportunity`:
- Summary ("Why Colt? Why Now?")
- **Hooks**, **Executive Narratives**, **Regulatory Triggers**, **AI Urgency**, **Competitive Displacement Angles**, **Clear Colt Differentiation** — include every bullet from source with citations preserved
- **Use Case Recommendations Table:** include all recommendations from alignment output

## 12. Signals
Write three comprehensive prose paragraphs (NO bullet points, dashes, or numbered lists under this heading):
- Paragraph 1 — **Growth Signals:** weave in **every** growth/hiring/M&A/expansion signal from `growthsignals_output` with inline **https://** URLs where available
- Paragraph 2 — **Risk Signals:** weave in **every** risk/regulatory/security/compliance signal from `risksignals_output` with inline URLs
- Paragraph 3 — **Campaign Signals:** weave in **every** campaign/advertising/brand signal from `campaignsignals_output` with inline URLs
Do not omit signals to keep paragraphs short — use multiple sentences as needed.

## 13. Source Summary
Reproduce the **Verified Source URLs** list provided above, one URL per line, de-duplicated. This is the authoritative and complete source list — do not add, omit, or invent URLs, and do not attempt to re-extract links from the prose sections above. Omit this section only if the Verified Source URLs block above is empty.

---
Output ONLY the markdown report with above structure -- no commentary, no code fences.
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


@dataclass(frozen=True, slots=True)
class _RevisionState:
    """Feedback from a failed ReportCompiler attempt, threaded to the next
    retry attempt's to_input() call so the revision is targeted rather
    than a blind full regeneration.

    Stored in ReportCompiler._revisions, a dict keyed by id(request)
    (CompilerInput is an immutable frozen dataclass, one instance per job
    -- see ResearchPipeline.run()). A plain contextvars.ContextVar was
    tried first and confirmed (via a standalone asyncio experiment) NOT to
    work here: Agent.run()'s retry loop calls
    `await asyncio.wait_for(self.execute(request), ...)` on every attempt,
    and asyncio.wait_for() wraps its coroutine in a *new* Task, which
    receives only a *copy* of the calling context -- a ContextVar.set()
    inside attempt N's execute() is therefore invisible to attempt N+1's
    to_input(). Keying a plain dict by id(request) on the ReportCompiler
    instance works because dict mutation is a side effect on a shared
    object, not context propagation, and id(request) safely disambiguates
    concurrent jobs sharing this singleton instance (see
    dependencies.py::build_research_pipeline) since each job constructs
    its own distinct CompilerInput.
    """

    feedback: str
    draft: str


# Bounds the one-entry-per-permanently-failed-job leak in
# ReportCompiler._revisions described in _RevisionState's docstring above.
_MAX_TRACKED_REVISIONS = 50


def _render_revision_block(feedback: str, previous_draft: str) -> str:
    """Render the retry-revision instructions appended to the prompt when a
    prior attempt's draft failed validation.

    This is what makes a ReportCompiler retry a targeted *revision* of the
    previous draft (agent's own prior output as the next step's starting
    point) rather than a blind full regeneration from the same inputs --
    the model sees exactly what was wrong and the text it needs to fix.
    """
    return f"""

---

## REVISION REQUIRED

Your previous draft of this report failed output validation with the
following issue(s):
{feedback}

Here is your previous draft, for reference:

---BEGIN PREVIOUS DRAFT---
{previous_draft}
---END PREVIOUS DRAFT---

Produce a corrected report that fixes ONLY the validation issue(s) listed
above. Preserve all correct content from the previous draft verbatim where
possible -- do not regenerate unrelated sections from scratch, and do not
introduce any new facts beyond what is present in the target account
inputs or Colt catalog knowledge above.

Output ONLY the corrected markdown report -- no commentary, no code fences.
"""


def _render_evidence_urls_block(findings) -> str:  # noqa: ANN001
    """Render the real Google Search grounding citation URLs captured on
    each DomainFinding.evidence, de-duplicated and sorted.

    Fixes a bug found in live testing (2026-08-30): the compiler prompt
    only ever received finding.content (prose text) and was instructed to
    "extract every web URL from all injected agent outputs" by re-scanning
    that prose. Since SearchExecutor's grounding evidence (real citation
    URLs from response.candidates[0].grounding_metadata) was never
    rendered into the prompt at all, the model could only find URLs that
    happened to appear as inline markdown links in the prose -- on a live
    Unilever run this surfaced only 13 of the many available citation
    URLs, all from one domain's incidental "Sources" subsection, while
    the other 11 domains' real grounding evidence was invisible to it.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for finding in findings.domains.values():
        for evidence in finding.evidence:
            if evidence.url and evidence.url not in seen:
                seen.add(evidence.url)
                urls.append(evidence.url)
    if not urls:
        return "(no grounding citation URLs available)"
    return "\n".join(f"- {url}" for url in sorted(urls))


class ReportCompiler(AdkAgentStep[CompilerInput, Report]):
    """Compiles the final markdown report from exactly findings + alignment."""

    name = "ReportCompiler"

    def __init__(
        self, *, model: str | None = None, retry: RetryPolicy | None = None
    ) -> None:
        self._model = model or settings.GEMINI_MODEL
        self._guardrail = OutputGuardrail()
        self._bm25_verifier = Bm25Verifier()
        if retry is not None:
            self.retry = retry
        # Retry-revision feedback keyed by id(request); see _RevisionState
        # docstring for why this (rather than a ContextVar or a plain
        # instance attribute) is the correct concurrency-safe choice for a
        # singleton ReportCompiler instance.
        self._revisions: dict[int, _RevisionState] = {}

    def build_agent(self) -> LlmAgent:
        return LlmAgent(
            name=self.name,
            model=RegionalGemini(model=self._model, retry_options=retry_config),
            instruction="You are the Report Compiler for Colt Technology Services.",
            tools=[],
            output_key="final_report",
            include_contents="none",
            description="Compiles the final markdown report from findings and alignment.",
            generate_content_config=get_safety_config_for_agent(self.name),
        )

    def to_input(self, request: CompilerInput) -> str:
        alignment = request.alignment
        ev_block = _render_evidence_urls_block(request.findings)
        ev_count = len(request.findings.all_evidence())
        logger.info(
            f"[ReportCompiler] Rendering prompt for '{request.company}': "
            f"{len(request.findings.domains)} domains, {ev_count} total evidence citations in input"
        )
        prompt = _REPORT_COMPILER_PROMPT_TEMPLATE.format(
            company=request.company,
            domain_findings_block=_render_domain_findings_block(request.findings),
            alignment_mappings_block=_render_alignment_mappings_block(alignment),
            opportunity_summary=alignment.opportunity_summary,
            hooks_block=", ".join(alignment.hooks) or "(none)",
            evidence_urls_block=ev_block,
        )
        # Consumed on read (pop, not get): once this attempt's prompt is
        # built from it, the entry is no longer needed. If this attempt
        # also fails, execute() below writes a fresh entry for the next
        # retry; if it succeeds, nothing is left behind to clean up.
        state = self._revisions.pop(id(request), None)
        if state is not None:
            # Next-step-of-the-same-agent revision (per user requirement):
            # the retried attempt sees exactly what was wrong with its own
            # previous draft and is asked to fix it, rather than
            # regenerating the whole report blind from the same inputs.
            prompt += _render_revision_block(state.feedback, state.draft)
        return prompt

    def to_output(self, raw: Any, usage: tuple[int, int]) -> Report:
        markdown = clean_markdown_report(str(raw))
        return Report(markdown=markdown, validation_status="PENDING")

    async def execute(self, request: CompilerInput) -> Report:
        report = await super().execute(request)
        sec13_match = re.search(
            r"##\s*13\.?\s*Source\s+Summary(.*?)(?=\n##|\Z)",
            report.markdown,
            re.IGNORECASE | re.DOTALL,
        )
        sec13_text = sec13_match.group(1).strip() if sec13_match else ""
        sec13_urls = re.findall(r"https?://[^\s\)\]\,\"\'\<\>]+", sec13_text)
        logger.info(
            f"[ReportCompiler] Draft received for '{request.company}': "
            f"{len(report.markdown)} chars, Section 13 has {len(sec13_urls)} URLs "
            f"({len(set(sec13_urls))} unique)"
        )
        result = await self._guardrail.validate(report.markdown)
        violations = [{"rule": v.rule, "detail": v.detail} for v in result.violations]
        is_valid = result.is_valid

        # Second, stronger gate on top of OutputGuardrail's structural
        # checks (headers/tables present): OutputGuardrail can pass a
        # report whose section content is fabricated. Bm25Verifier scores
        # each factual sentence in the draft against the real Google
        # Search grounding evidence captured by SearchExecutor -- a
        # structurally-perfect but mostly-ungrounded report still fails
        # here and triggers a revision retry (see to_input()/execute()
        # revision-feedback loop above).
        if settings.REPORT_COMPILER_BM25_GATE_ENABLED:
            bm25_state = _build_bm25_state(request.findings)
            bm25_result = self._bm25_verifier.verify(
                report.markdown, bm25_state, agent_name=_BM25_VERIFIER_AGENT_NAME
            )
            if bm25_result.status == "FAILED":
                is_valid = False
                sample = "; ".join(bm25_result.unsupported[:3])
                violations.append(
                    {
                        "rule": "bm25_groundedness",
                        "detail": (
                            f"{len(bm25_result.unsupported)} claim(s) not "
                            f"supported by search evidence, e.g.: {sample}"
                        ),
                    }
                )

        violations = tuple(violations)
        status = "PASSED" if is_valid else "FAILED"
        # validate() (called by Agent.run() right after this returns) will
        # raise InvalidOutputError on FAILED, triggering a step-level retry.
        # Stash this draft + its violations here (rather than in
        # validate(), which only decides whether to retry and has no hook
        # into the next attempt) so the *next* retry attempt's to_input()
        # call -- Agent.run() re-invokes execute(request) with the same
        # CompilerInput object -- can build a revision prompt from them.
        if status == "FAILED":
            self._revisions[id(request)] = _RevisionState(
                feedback="\n".join(f"- {v['rule']}: {v['detail']}" for v in violations),
                draft=report.markdown,
            )
            # Entries are normally popped by to_input() on the next retry
            # attempt (see there). The one case that leaves an entry
            # behind is a *permanent* failure (retries exhausted): Agent
            # .run() raises AgentError instead of calling to_input()
            # again, so nothing ever consumes it. This cap bounds that
            # leak to a handful of small objects regardless of how many
            # jobs this singleton instance processes over its lifetime.
            if len(self._revisions) > _MAX_TRACKED_REVISIONS:
                oldest_key = next(iter(self._revisions))
                self._revisions.pop(oldest_key, None)
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
