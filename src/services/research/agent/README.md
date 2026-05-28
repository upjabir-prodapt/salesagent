# Research Agent (ADK)

This package contains ADK runtime logic and evaluation components used by the research service pipeline.

## Runtime Boundaries

| Path | Role |
|------|------|
| `sales/` | Sales-specific graph composition, prompts, schemas, tools, sub-agents |
| `utils/` | Callback facade, callback handlers, retry pipeline, telemetry, safety contracts |
| `evaluation_service.py` | Post-run report scoring (LLM judge + automated metrics) |
| `evaluation_section_a.py` | Section A scoring parser and weighted rubric |
| `evaluation_section_b.py` | Section B computed metrics and completeness/groundedness |
| `session_service_factory.py` | ADK session backend selection |

## Current Composition Path

- `src/services/research/agents/app_factory.py` builds the ADK app through `PlanReActAgentFactory` and `AgentRegistry`.
- `src/services/research/runner_service.py` executes the app and streams ADK events.
- `src/services/research/agent/utils/agent_pipeline.py` applies per-agent retry and output contract validation.

Catalog vector search is implemented in `src/services/catalog/search.py` and surfaced to synthesis via sales tools.
