from unittest.mock import MagicMock, patch

import pytest

from src.core.exceptions import ServiceError
from src.services.research.service import ResearchService


def test_new_job_id_uses_prefix(mock_settings):
    service = ResearchService(MagicMock(), MagicMock())
    job_id = service.new_job_id()
    assert job_id.startswith("job_")


def test_create_research_request_success():
    bq = MagicMock()
    bq.create_request.return_value = True
    service = ResearchService(bq, MagicMock())
    assert service.create_research_request("job_1", "Acme") is True
    bq.create_request.assert_called_once()


def test_create_research_request_wraps_errors():
    bq = MagicMock()
    bq.create_request.side_effect = RuntimeError("db down")
    service = ResearchService(bq, MagicMock())
    with pytest.raises(ServiceError, match="Failed to create research request"):
        service.create_research_request("job_1", "Acme")


def test_get_request_status_and_result():
    bq = MagicMock()
    bq.get_status.return_value = {"status": "PROCESSING"}
    bq.get_request_result.return_value = {"metadata": {"tokens_used": 10}}
    service = ResearchService(bq, MagicMock())

    assert service.get_request_status("job_1")["status"] == "PROCESSING"
    result = service.get_request_result("job_1")
    assert result is not None
    assert "model_card" in result


def test_get_pdf_report_completed():
    bq = MagicMock()
    bq.get_status.return_value = {"status": "COMPLETED", "company_name": "Acme"}
    gcs = MagicMock()
    gcs.download_pdf.return_value = b"%PDF"
    service = ResearchService(bq, gcs)
    pdf, name = service.get_pdf_report("job_1")
    assert pdf == b"%PDF"
    assert name == "Acme"


@pytest.mark.asyncio
async def test_process_research_background_delegates():
    bq = MagicMock()
    gcs = MagicMock()
    service = ResearchService(bq, gcs)
    with patch.object(
        service._application,
        "run_background_job",
        return_value=None,
    ) as run_background_job:
        await service.process_research_background("job_1", "Acme", metadata={"k": "v"})
    run_background_job.assert_called_once()


def test_get_request_status_wraps_errors():
    bq = MagicMock()
    bq.get_status.side_effect = RuntimeError("db down")
    service = ResearchService(bq, MagicMock())
    with pytest.raises(ServiceError, match="Failed to get job status"):
        service.get_request_status("job_1")


def test_get_pdf_report_variants():
    bq = MagicMock()
    gcs = MagicMock()
    service = ResearchService(bq, gcs)

    bq.get_status.return_value = None
    assert service.get_pdf_report("job_1") is None

    bq.get_status.return_value = {"status": "PROCESSING"}
    with pytest.raises(ServiceError, match="PDF not available"):
        service.get_pdf_report("job_1")

    bq.get_status.side_effect = RuntimeError("bq error")
    with pytest.raises(ServiceError, match="Failed to fetch job status"):
        service.get_pdf_report("job_1")

    bq.get_status.side_effect = None
    bq.get_status.return_value = {"status": "COMPLETED", "company_name": "Acme"}
    gcs.download_pdf.side_effect = RuntimeError("gcs error")
    with pytest.raises(ServiceError, match="Failed to download PDF"):
        service.get_pdf_report("job_1")

    gcs.download_pdf.side_effect = None
    gcs.download_pdf.return_value = None
    with pytest.raises(ServiceError, match="PDF file not found"):
        service.get_pdf_report("job_1")


def test_get_request_result_none_and_error():
    bq = MagicMock()
    bq.get_request_result.return_value = None
    service = ResearchService(bq, MagicMock())
    assert service.get_request_result("job_1") is None

    bq.get_request_result.side_effect = RuntimeError("db down")
    with pytest.raises(ServiceError, match="Failed to get job result"):
        service.get_request_result("job_1")
