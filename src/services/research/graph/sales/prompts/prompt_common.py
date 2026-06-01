"""Shared PlanReAct prompt block for research agents."""

from google.adk.planners.plan_re_act_planner import (
    ACTION_TAG,
    FINAL_ANSWER_TAG,
    PLANNING_TAG,
    REPLANNING_TAG,
)

from ..tools.search import SEARCH_AGENT_NAME

# Working draft tag — synthesise tool/model evidence before verification (not the final output).
AGGREGATED_ANSWER_TAG = "/*AGGREGATED_ANSWER*/"

PLAN_REACT_RESEARCH_BLOCK = f"""
## Tools and evidence
- Use only `{SEARCH_AGENT_NAME}` for facts. Pass a clear `request=<search query>` each time.
- Do not use unstated assumptions, prior training knowledge, or generic industry filler.
- Every claim must be traceable to snippets returned in this session.

## Required workflow (aggregate → verify → finalise)

**Do not emit {FINAL_ANSWER_TAG} until `verify_draft_answer` returns PASSED.**

1. {PLANNING_TAG} — Map every **Required Data** field (in your agent task above) to planned searches. Run as many distinct `{SEARCH_AGENT_NAME}` calls as needed until each field is evidenced or explicitly marked `publicly unavailable` after exhaustive, targeted queries (initial plan plus any {REPLANNING_TAG} cycles).
2. {ACTION_TAG} — Call `{SEARCH_AGENT_NAME}(request=...)` for evidence. One focused query per call.
3. {AGGREGATED_ANSWER_TAG} — **Aggregated answer (working draft):** combine all tool results and model synthesis into one complete JSON draft matching your output schema. Tag sources in JSON fields. This is **not** the final output.
4. {ACTION_TAG} — Call `verify_draft_answer(draft=<full aggregated answer text>)`.
5. If verification returns **FAILED**: {REPLANNING_TAG} — plan targeted `{SEARCH_AGENT_NAME}` queries for unsupported claims only, run them under {ACTION_TAG}, revise the **aggregated answer**, and call `verify_draft_answer` again. Repeat until PASSED or you exhaust replan attempts.
6. Only after **PASSED**: emit {FINAL_ANSWER_TAG} with the **same verified aggregated answer** — copy it exactly; **no new searches, no new facts, no edits**.

## Phase rules

| Phase | Your job |
|-------|----------|
| {PLANNING_TAG} | List missing or weak **Required Data** fields. Add one planned search per gap. Prefer sources listed under **Target Sources** in your task. |
| {ACTION_TAG} | Execute planned queries. **Drilling:** if a snippet cites an annual report, filing, or strategy PDF, run a follow-up `{SEARCH_AGENT_NAME}` query using the document title or a quoted phrase — you cannot open URLs directly. |
| {AGGREGATED_ANSWER_TAG} | **Aggregated answer:** merge session search evidence into complete JSON per schema. Use `"publicly unavailable"` only after exhaustive search. No "likely", "typically", "probably", or estimates. Verbatim quotes only. Company-specific facts only. **Never use this tag for the final deliverable.** |
| `verify_draft_answer` | Submit the **entire aggregated answer** text. If **FAILED**, read `unsupported` — do **not** emit {FINAL_ANSWER_TAG}; go to {REPLANNING_TAG}. |
| {REPLANNING_TAG} | Targeted searches for failed claims only, then produce a **revised aggregated answer** and re-verify. |
| {FINAL_ANSWER_TAG} | **Finalised answer only:** output the PASSED aggregated answer unchanged — valid JSON per schema below. Emit this tag **once**, after verification PASSED. |

## Anti-hallucination (mandatory)

- **No training data** as a source; session search evidence only.
- **No interpolation** of missing figures.
- **Mandatory source tagging** — include the full **https://** URL from `{SEARCH_AGENT_NAME}` snippets on every factual JSON field where a URL is available; otherwise cite publication name and search query used.
- **No generic industry claims** without company-specific search proof.
- **Exact quotes only** for executive or strategic commentary.
- Violations cause downstream report rejection.
"""

RESEARCH_GUIDELINES = PLAN_REACT_RESEARCH_BLOCK

RESEARCH_GUIDELINES = PLAN_REACT_RESEARCH_BLOCK
