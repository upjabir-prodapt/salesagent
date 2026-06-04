from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.dependencies import handler_dependencies as deps
from src.handlers.catalog_handler import CatalogHandler
from src.handlers.research_handler import ResearchHandler


def test_get_research_handler_returns_handler_with_service() -> None:
    mock_service = MagicMock()
    with patch.object(deps, "get_research_service", return_value=mock_service):
        handler = deps.get_research_handler()

    assert isinstance(handler, ResearchHandler)
    assert handler._service is mock_service


def test_get_catalog_handler_returns_handler_with_service() -> None:
    mock_service = MagicMock()
    with patch.object(deps, "get_catalog_service", return_value=mock_service):
        handler = deps.get_catalog_handler()

    assert isinstance(handler, CatalogHandler)
    assert handler._service is mock_service
