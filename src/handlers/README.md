# Handlers

HTTP request handlers sit between FastAPI routes and domain services. Routes stay thin (routing, DI, OpenAPI); handlers own request validation orchestration, tracing at accept time, and response mapping.

## Layout

| Handler | Service | Routes |
|---------|---------|--------|
| `ResearchHandler` | `services.research.ResearchService` | `routes/research.py` |
| `CatalogHandler` | `services.catalog.CatalogService` | `routes/catalog.py` |

## Wiring

```python
# dependencies/handler_dependencies.py
def get_research_handler() -> ResearchHandler:
    return ResearchHandler(get_research_service())
```

```python
# routes/research.py
@router.post("/initiate")
async def initiate_research(..., handler: ResearchHandlerDep):
    return await handler.initiate_research(...)
```
