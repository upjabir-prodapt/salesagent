# Research agent (ADK)

Sales research agent graph and ADK runtime utilities. Used by `ResearchService` / `RunnerService` in the parent `research/` package.

## Layout

| Path | Role |
|------|------|
| `sales/` | ADK agent graph (`create_sales_agent_app`), prompts, sub-agents, sales-specific utils |
| `evaluation_service.py` | Post-run report quality evaluation |
| `session_service_factory.py` | ADK session backend selection |
| `session_ids.py` | Runner session id helpers |
| `utils/` | Shared ADK callbacks, pipeline retries, telemetry, safety |

## Imports

```python
from src.services.research.agent.sales.agent import create_sales_agent_app
from src.services.research.agent.evaluation_service import EvaluationService
from src.services.research.agent.utils.agent_pipeline import run_runner_with_per_agent_retry
```

Catalog vector search lives in `src/services/catalog/search.py`.
