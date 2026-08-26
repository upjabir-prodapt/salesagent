from unittest.mock import MagicMock

import pytest

from src.api.services.research_job_service import ResearchJobService
from src.shared.exceptions import ServiceError


def test_job_service_new_job_id_uses_prefix(mock_settings):
    service = ResearchJobService(MagicMock(), MagicMock())
    job_id = service.new_job_id()
    assert job_id.startswith("job_")


def test_job_service_create_research_request_success():
    bq = MagicMock()
    bq.create_request.return_value = True
    service = ResearchJobService(bq, MagicMock())
    assert service.create_research_request("job_1", "Acme") is True
    bq.create_request.assert_called_once()


def test_job_service_create_research_request_wraps_errors():
    bq = MagicMock()
    bq.create_request.side_effect = RuntimeError("db down")
    service = ResearchJobService(bq, MagicMock())
    with pytest.raises(ServiceError, match="Failed to create research request"):
        service.create_research_request("job_1", "Acme")


def test_job_service_mark_job_failed():
    bq = MagicMock()
    service = ResearchJobService(bq, MagicMock())
    service.mark_job_failed("job_1", "Fatal error")
    bq.update_status.assert_called_once_with("job_1", "FAILED", error="Fatal error")


def test_job_service_get_request_status_and_result():
    bq = MagicMock()
    bq.get_status.return_value = {"status": "PROCESSING"}
    bq.get_request_result.return_value = {"metadata": {"tokens_used": 10}}
    service = ResearchJobService(bq, MagicMock())

    assert service.get_request_status("job_1")["status"] == "PROCESSING"
    result = service.get_request_result("job_1")
    assert result is not None
    assert "model_card" in result


def test_job_service_get_pdf_report_completed():
    bq = MagicMock()
    bq.get_status.return_value = {"status": "COMPLETED", "company_name": "Acme"}
    gcs = MagicMock()
    gcs.download_pdf.return_value = b"%PDF"
    service = ResearchJobService(bq, gcs)
    pdf, name = service.get_pdf_report("job_1")
    assert pdf == b"%PDF"
    assert name == "Acme"


def test_job_service_submit_feedback():
    bq = MagicMock()
    bq.get_status.return_value = {"status": "COMPLETED"}
    bq.insert_user_feedback.return_value = True
    service = ResearchJobService(bq, MagicMock())
    assert service.submit_feedback("job_1", "Great report", "user@colt.net") is True
    bq.insert_user_feedback.assert_called_once_with(
        "job_1", "user@colt.net", "Great report"
    )
