# Catalog services

Unified package for the Colt product catalog vector index and HTTP job API.

| Module | Responsibility |
|--------|----------------|
| `service.py` | Job orchestration (`CatalogService`): rebuild, search, GCS uploads |
| `pipeline.py` | End-to-end index build (chunk → embed → publish → Vertex deploy) |
| `search.py` | Runtime vector search (`colt_product_search`) used by agents and the search API |
| `chunking.py`, `embeddings.py`, `storage.py`, `paths.py`, `vertex.py` | Pipeline building blocks |

## Imports

```python
from src.services.catalog import CatalogService, VectorCatalogPipeline
from src.services.catalog.search import colt_product_search
```

Research agents import `colt_product_search` from here (not the other way around).
