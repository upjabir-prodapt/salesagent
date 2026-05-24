from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.research.research_service import ResearchService
from src.core.exceptions import ServiceError


@pytest.fixture
def mock_bq_repo():
    return MagicMock()


@pytest.fixture
def mock_gcs_repo():
    return MagicMock()


@pytest.fixture
def service(mock_bq_repo, mock_gcs_repo):
    return ResearchService(
        bigquery_repository=mock_bq_repo, gcs_repository=mock_gcs_repo
    )


@pytest.mark.asyncio
async def test_create_research_request_success(service, mock_bq_repo):
    mock_bq_repo.create_request.return_value = True
    result = service.create_research_request("job_123", "Acme Corp")
    assert result is True
    mock_bq_repo.create_request.assert_called_once_with(
        job_id="job_123", company_name="Acme Corp", metadata=None
    )


@pytest.mark.asyncio
async def test_create_research_request_failure(service, mock_bq_repo):
    mock_bq_repo.create_request.side_effect = Exception("DB Error")
    with pytest.raises(ServiceError):
        service.create_research_request("job_123", "Acme Corp")


@pytest.mark.asyncio
async def test_process_research_background_orchestration(
    service, mock_bq_repo, mock_settings
):
    # Setup mocks for internal calls
    service._runner.run = AsyncMock(
        return_value=(
            "# Report",
            {"mc_input_tokens": 10, "report_validation_status": "PASSED"},
        )
    )
    with patch(
        "src.services.research.finalization_service.EvaluationService"
    ) as mock_eval_cls:
        mock_eval_cls.return_value.evaluate = AsyncMock(
            return_value={"final_composite_score": 0.9}
        )
        with patch.object(
            service._finalization, "generate_pdf", return_value=b"pdf"
        ):
            await service.process_research_background("job_123", "Acme Corp")

    assert mock_bq_repo.update_status.called
    found_completion = False
    for call in mock_bq_repo.update_status.call_args_list:
        args, kwargs = call
        if args == ("job_123", "COMPLETED") and kwargs.get("progress") == 100:
            found_completion = True
            break
    assert found_completion, "Completion call to update_status not found"
