import pytest
from unittest.mock import MagicMock, AsyncMock, patch, ANY
from src.agents.research_service import ResearchService
from src.core.exceptions import ServiceError

@pytest.fixture
def research_service():
    mock_bq = MagicMock()
    mock_gcs = MagicMock()
    return ResearchService(bigquery_repository=mock_bq, gcs_repository=mock_gcs)

@pytest.mark.asyncio
async def test_create_research_request_success(research_service):
    research_service.bigquery_repo.create_request.return_value = True
    
    result = await research_service.create_research_request(
        job_id="test-job", company_name="Test Company"
    )
    
    assert result is True
    research_service.bigquery_repo.create_request.assert_called_once_with(
        job_id="test-job", company_name="Test Company", metadata=None
    )

@pytest.mark.asyncio
async def test_create_research_request_failure(research_service):
    research_service.bigquery_repo.create_request.side_effect = Exception("BQ Error")
    
    with pytest.raises(ServiceError):
        await research_service.create_research_request(
            job_id="test-job", company_name="Test Company"
        )

@pytest.mark.asyncio
async def test_process_research_background_success(research_service, mock_settings):
    # Mocking internal methods and dependencies
    job_id = "test-job"
    company_name = "Test Company"
    final_report = "# Final Report"
    session_state = {"mc_input_tokens": 100, "mc_output_tokens": 50}
    
    with patch.object(research_service, "_run_sales_agent", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = (final_report, session_state)
        
        with patch("src.agents.research_service.OutputGuardrail") as mock_guardrail_cls:
            mock_guardrail = MagicMock()
            mock_guardrail.validate = AsyncMock()
            mock_guardrail.validate.return_value.is_valid = True
            mock_guardrail_cls.return_value = mock_guardrail
            
            # Mock EvaluationService to avoid real calls
            with patch("src.agents.research_service.EvaluationService") as mock_eval_cls:
                mock_eval_service = MagicMock()
                mock_eval_service.evaluate = AsyncMock(return_value={"final_composite_score": 0.9})
                mock_eval_cls.return_value = mock_eval_service
                
                await research_service.process_research_background(job_id, company_name)
                
                # Verify repository calls
                research_service.bigquery_repo.update_status.assert_any_call(
                    job_id, "PROCESSING", progress=mock_settings.RESEARCH_INIT_PROGRESS,
                    current_step=mock_settings.RESEARCH_INIT_STEP_LABEL
                )
                research_service.gcs_repo.upload_json.assert_called_once_with(job_id, session_state)
                research_service.gcs_repo.upload_markdown.assert_called_once_with(job_id, final_report)
                research_service.bigquery_repo.update_status.assert_any_call(
                    job_id, "COMPLETED", gcs_uri=research_service.gcs_repo.upload_markdown.return_value,
                    progress=100, current_step="Completed", metadata_update=ANY
                )
