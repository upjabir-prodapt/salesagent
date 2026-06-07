from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.research.finalization.service import ResearchFinalizationService


@pytest.mark.asyncio
async def test_finalize_runs_all_ops_successfully() -> None:
    bq = MagicMock()
    gcs = MagicMock()
    service = ResearchFinalizationService(bq, gcs)

    with (
        patch(
            "src.services.research.finalization.service.run_pdf_op",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.services.research.finalization.service.run_evaluation_op",
            new_callable=AsyncMock,
        ),
        patch(
            "src.services.research.finalization.service.run_cost_attribution_op",
            new_callable=AsyncMock,
        ),
        patch(
            "src.services.research.finalization.service.run_telemetry_flush_op",
            new_callable=AsyncMock,
        ),
    ):
        failures, pdf_ok = await service.finalize(
            "job-1", "# Report", {"agent_telemetry_records": []}, {"total_tokens": 10}
        )

    assert failures == {}
    assert pdf_ok is True


@pytest.mark.asyncio
async def test_finalize_records_side_op_failures() -> None:
    service = ResearchFinalizationService(MagicMock(), MagicMock())

    with (
        patch(
            "src.services.research.finalization.service.run_pdf_op",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pdf fail"),
        ),
        patch(
            "src.services.research.finalization.service.run_evaluation_op",
            new_callable=AsyncMock,
            side_effect=RuntimeError("eval fail"),
        ),
        patch(
            "src.services.research.finalization.service.run_cost_attribution_op",
            new_callable=AsyncMock,
            side_effect=RuntimeError("cost fail"),
        ),
        patch(
            "src.services.research.finalization.service.run_telemetry_flush_op",
            new_callable=AsyncMock,
            side_effect=RuntimeError("telemetry fail"),
        ),
    ):
        failures, pdf_ok = await service.finalize("job-2", "# R", {}, {})

    assert pdf_ok is False
    assert set(failures) == {
        "pdf",
        "evaluation",
        "cost_attribution",
        "telemetry",
    }


@pytest.mark.asyncio
async def test_export_failure_telemetry() -> None:
    service = ResearchFinalizationService(MagicMock(), MagicMock())

    with (
        patch(
            "src.services.research.finalization.service.run_cost_attribution_op",
            new_callable=AsyncMock,
        ) as cost_op,
        patch(
            "src.services.research.finalization.service.run_telemetry_flush_op",
            new_callable=AsyncMock,
        ) as telemetry_op,
    ):
        failures = await service.export_failure_telemetry("job-3", {}, {})

    assert failures == {}
    cost_op.assert_awaited_once()
    telemetry_op.assert_awaited_once()


def test_generate_pdf_returns_bytes() -> None:
    mock_html = MagicMock()
    mock_html.write_pdf.return_value = b"%PDF-1.4"
    mock_markdown = MagicMock()
    mock_markdown.markdown.return_value = "<h1>Title</h1><p>Body</p>"
    mock_weasyprint = MagicMock()
    mock_weasyprint.HTML.return_value = mock_html
    mock_weasyprint.CSS.return_value = MagicMock()

    with patch.dict(
        sys.modules,
        {"markdown": mock_markdown, "weasyprint": mock_weasyprint},
    ):
        result = ResearchFinalizationService.generate_pdf("# Title\n\nBody")

    assert result == b"%PDF-1.4"
    mock_markdown.markdown.assert_called_once()
    mock_weasyprint.HTML.assert_called_once()
    mock_html.write_pdf.assert_called_once()
