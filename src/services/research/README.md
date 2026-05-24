# Research services

Job orchestration for the sales research API. HTTP: `src/routes/research.py` → `src/handlers/research_handler.py`; ADK agents: `agent/sales/`.

## Modules

| Module | Responsibility |
|--------|----------------|
| `research_service.py` | Orchestrator: background job, validation gate, completion |
| `runner_service.py` | ADK Runner lifecycle and event streaming |
| `progress.py` | Debounced BigQuery progress + agent guardrails on events |
| `artifact_service.py` | GCS uploads (report, session, per-agent outputs) |
| `finalization_service.py` | PDF, evaluation, cost attribution, telemetry flush |
| `metrics.py` | Model card metrics and cost reconciliation |
| `retry.py` | Async retry helpers for side operations |
| `agent/` | ADK sales agent graph, evaluation, session factory, pipeline utils |

## Tracing

Service methods use `@traced` / `@traced_with_context` from `src/utils/tracing.py`. Span names follow `research.<area>.<action>`.
