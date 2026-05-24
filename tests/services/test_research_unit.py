from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.research.research_service import ResearchService


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def mock_gcs():
    return MagicMock()


@pytest.fixture
def service(mock_repo, mock_gcs):
    return ResearchService(bigquery_repository=mock_repo, gcs_repository=mock_gcs)


@pytest.mark.asyncio
async def test_create_research_request(service, mock_repo):
    mock_repo.create_request.return_value = True
    result = service.create_research_request("job_1", "Acme")
    assert result is True


@pytest.mark.asyncio
async def test_process_research_background_full_flow(
    service, mock_repo, mock_gcs, mock_settings
):
    # Mocking real LLM calls and PDF generation
    with (
        patch.object(service._runner, "run", new_callable=AsyncMock) as mock_run,
        patch("src.services.research.finalization_service.EvaluationService") as mock_eval,
    ):
        mock_run.return_value = (
            "# Report",
            {"mc_input_tokens": 10, "report_validation_status": "PASSED"},
        )
        mock_eval.return_value.evaluate = AsyncMock(
            return_value={"final_composite_score": 0.8}
        )

        # Avoid PDF generation for unit test
        with patch.object(
            service._finalization, "generate_pdf", return_value=b"pdf"
        ) as mock_pdf:
            mock_pdf.return_value = b"pdf"

            await service.process_research_background("job_1", "Acme")

            # Verify milestones were recorded
            assert mock_repo.update_status.call_count >= 2
            mock_gcs.upload_markdown.assert_called_once()
            mock_gcs.upload_evaluation.assert_called_once()
            # In research_service.py it calls insert_cost_attribution
            mock_repo.insert_cost_attribution.assert_called_once()


from unittest.mock import ANY


@pytest.mark.asyncio
async def test_get_request_status(service, mock_repo):
    mock_repo.get_status.return_value = {"status": "PENDING"}
    result = service.get_request_status("job_1")
    assert result["status"] == "PENDING"


@pytest.mark.asyncio
async def test_get_request_result(service, mock_repo):
    mock_repo.get_request_result.return_value = {"status": "COMPLETED"}
    result = service.get_request_result("job_1")
    assert result["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_process_research_background_guardrail_failure(
    service, mock_repo, mock_gcs, mock_settings
):
    with patch.object(service._runner, "run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (
            "# Bad Report",
            {
                "report_validation_status": "FAILED",
                "report_validation_violations": [
                    {"rule": "output:missing_table", "detail": "fail"}
                ],
            },
        )

        # main loop catches failure and returns None, doesn't raise exception to top level
        await service.process_research_background("job_1", "Acme")

        # Verify it marked as FAILED in repo
        mock_repo.update_status.assert_any_call(
            "job_1", "FAILED", error=ANY, metadata_update=ANY
        )
