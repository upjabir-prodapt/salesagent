from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.research_service import ResearchService
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
    service._run_sales_agent = AsyncMock(
        return_value=("# Report", {"mc_input_tokens": 10})
    )
    service._generate_and_upload_pdf = AsyncMock(return_value="gs://pdf")
    service._run_evaluation = AsyncMock(return_value={"score": 0.9})

    with patch("src.agents.research_service.OutputGuardrail") as mock_og:
        mock_og.return_value.validate = AsyncMock(return_value=MagicMock(is_valid=True))

        await service.process_research_background("job_123", "Acme Corp")

        # Verify status updates
        assert mock_bq_repo.update_status.called
        # Verify final completion - checking the core positional and some keyword args
        # The actual call has many more keyword args (metadata)
        found_completion = False
        for call in mock_bq_repo.update_status.call_args_list:
            args, kwargs = call
            if args == ("job_123", "COMPLETED") and kwargs.get("progress") == 100:
                found_completion = True
                break
        assert found_completion, "Completion call to update_status not found"
