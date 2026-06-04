from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.core.exceptions import ResourceNotFoundError
from src.handlers.research_handler import ResearchHandler


def test_get_research_result_not_found() -> None:
    service = MagicMock()
    service.get_request_result.return_value = None
    handler = ResearchHandler(service)

    with pytest.raises(ResourceNotFoundError):
        handler.get_research_result("missing-job")


def test_download_pdf_report_not_found() -> None:
    service = MagicMock()
    service.get_pdf_report.return_value = None
    handler = ResearchHandler(service)

    with pytest.raises(ResourceNotFoundError):
        handler.download_pdf_report("missing-job")
