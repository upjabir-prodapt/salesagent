from unittest.mock import MagicMock

import pytest

from src.api.services.research_job_service import ResearchJobService
from src.shared.exceptions import ResourceNotFoundError, ServiceError


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
    bq.get_status.return_value = {"status": "PROCESSING", "user_id": "user@colt.net"}
    bq.get_request_result.return_value = {
        "metadata": {"tokens_used": 10},
        "user_id": "user@colt.net",
    }
    service = ResearchJobService(bq, MagicMock())

    assert (
        service.get_request_status("job_1", user_email="user@colt.net")["status"]
        == "PROCESSING"
    )
    result = service.get_request_result("job_1", user_email="user@colt.net")
    assert result is not None
    assert "model_card" in result


def test_job_service_ownership_mismatch_raises_404(mock_settings):
    mock_settings.IS_LOCAL = False
    bq = MagicMock()
    bq.get_status.return_value = {"status": "PROCESSING", "user_id": "owner@colt.net"}
    service = ResearchJobService(bq, MagicMock())

    with pytest.raises(ResourceNotFoundError, match="Job job_1 not found"):
        service.get_request_status("job_1", user_email="attacker@colt.net")


def test_job_service_get_pdf_report_completed():
    bq = MagicMock()
    bq.get_status.return_value = {
        "status": "COMPLETED",
        "company_name": "Acme",
        "user_id": "user@colt.net",
    }
    gcs = MagicMock()
    gcs.download_pdf.return_value = b"%PDF"
    service = ResearchJobService(bq, gcs)
    pdf, name = service.get_pdf_report("job_1", user_email="user@colt.net")
    assert pdf == b"%PDF"
    assert name == "Acme"


def test_job_service_list_jobs():
    bq = MagicMock()
    bq.list_jobs_for_user.return_value = [{"job_id": "job_1", "status": "COMPLETED"}]
    service = ResearchJobService(bq, MagicMock())
    jobs = service.list_jobs("user@colt.net", limit=10, offset=0)
    assert len(jobs) == 1
    bq.list_jobs_for_user.assert_called_once_with(
        user_email="user@colt.net", limit=10, offset=0
    )


def test_job_service_cancel_job():
    bq = MagicMock()
    bq.cancel_job.return_value = True
    service = ResearchJobService(bq, MagicMock())
    assert service.cancel_job("job_1", "user@colt.net") is True
    bq.cancel_job.assert_called_once_with("job_1", user_email="user@colt.net")


def test_job_service_submit_feedback():
    bq = MagicMock()
    bq.get_status.return_value = {"status": "COMPLETED", "user_id": "user@colt.net"}
    bq.insert_user_feedback.return_value = True
    service = ResearchJobService(bq, MagicMock())
    assert service.submit_feedback("job_1", "Great report", "user@colt.net") is True
    bq.insert_user_feedback.assert_called_once_with(
        "job_1", "user@colt.net", "Great report"
    )
