"""Product catalog: vector index pipeline and job orchestration."""

from .pipeline import VectorCatalogPipeline
from .search import colt_product_search
from .service import CatalogService

__all__ = ["CatalogService", "VectorCatalogPipeline", "colt_product_search"]
