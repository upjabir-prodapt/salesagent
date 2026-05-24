"""Handler dependency injection for FastAPI routes."""

from ..handlers.catalog_handler import CatalogHandler
from ..handlers.research_handler import ResearchHandler
from .service_dependencies import get_catalog_service, get_research_service


def get_research_handler() -> ResearchHandler:
    return ResearchHandler(get_research_service())


def get_catalog_handler() -> CatalogHandler:
    return CatalogHandler(get_catalog_service())
