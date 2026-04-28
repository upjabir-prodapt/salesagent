import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from src.agents.research_service import ResearchService
from src.core.exceptions import ServiceError
from google.genai import types

@pytest.fixture
def mock_bq_repo():
    return MagicMock()

@pytest.fixture
def mock_gcs_repo():
    return MagicMock()

@pytest.fixture
def research_service(mock_bq_repo, mock_gcs_repo):
    return ResearchService(bigquery_repository=mock_bq_repo, gcs_repository=mock_gcs_repo)

@pytest.mark.asyncio
async def test_run_sales_agent_functional(research_service, mock_bq_repo):
    """Verify the functional flow of running the sales agent with event updates."""
    
    mock_runner = MagicMock()
    mock_session_service = AsyncMock()
    mock_runner.session_service = mock_session_service
    
    mock_session = MagicMock()
    mock_session.id = "sess_123"
    mock_session.state = {"final_report": "# Success Report", "other": "data"}
    mock_session_service.get_session.return_value = mock_session
    
    # Mock event stream
    async def mock_events(**kwargs):
        event1 = MagicMock()
        event1.author = "ResearchOrchestrator"
        event1.invocation_id = "inv_1"
        event1.is_final_response.return_value = False
        yield event1
        
        event2 = MagicMock()
        event2.author = "ResearchOrchestrator"
        event2.invocation_id = "inv_1"
        event2.is_final_response.return_value = True
        yield event2

    mock_runner.run_async = mock_events
    
    with patch("src.agents.research_service.Runner", return_value=mock_runner):
        with patch("src.agents.research_service.create_sales_agent_app"):
            report, state = await research_service._run_sales_agent("job_123", "Acme Corp")
            
            assert report == "# Success Report"
            assert state["final_report"] == "# Success Report"
            # Verify progress updates were called
            assert mock_bq_repo.update_status.called

@pytest.mark.asyncio
async def test_run_sales_agent_no_report_error(research_service, mock_bq_repo):
    """Verify ServiceError is raised when no report is generated."""
    mock_runner = MagicMock()
    mock_session_service = AsyncMock()
    mock_runner.session_service = mock_session_service
    
    mock_session = MagicMock()
    mock_session.state = {} # No final_report
    mock_session_service.get_session.return_value = mock_session
    
    async def empty_events(**kwargs):
        if False: yield None

    mock_runner.run_async = empty_events
    
    with patch("src.agents.research_service.Runner", return_value=mock_runner):
        with patch("src.agents.research_service.create_sales_agent_app"):
            with pytest.raises(ServiceError) as exc:
                await research_service._run_sales_agent("job_123", "Acme")
            assert "No final report generated" in str(exc.value)

@pytest.mark.asyncio
async def test_process_research_background_functional(research_service, mock_bq_repo, mock_gcs_repo):
    """Verify the functional background processing of research."""
    
    with patch.object(research_service, "_run_sales_agent", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = ("# Report", {"raw_search_cache": []})
        
        # Mocking OutputGuardrail to pass
        with patch("src.utils.guardrails.OutputGuardrail.validate") as mock_validate:
            mock_validate.return_value = MagicMock(is_valid=True)
            
            await research_service.process_research_background("job_123", "Acme")
            
            assert mock_bq_repo.update_status.called
            assert mock_gcs_repo.upload_json.called
            assert mock_gcs_repo.upload_markdown.called

@pytest.mark.asyncio
async def test_process_research_background_pdf_failure_functional(research_service, mock_bq_repo, mock_gcs_repo):
    """Functional test: PDF failure should be non-fatal."""
    with patch.object(research_service, "_run_sales_agent", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = ("# Report", {"mc_input_tokens": 10})
        with patch("src.utils.guardrails.OutputGuardrail.validate", return_value=MagicMock(is_valid=True)):
            # Force PDF generation failure
            with patch.object(research_service, "_generate_pdf_static", side_effect=Exception("PDF error")):
                await research_service.process_research_background("job_123", "Acme")
                # Still continues to status update
                assert mock_bq_repo.update_status.called

@pytest.mark.asyncio
async def test_process_research_background_eval_failure_functional(research_service, mock_bq_repo, mock_gcs_repo):
    """Functional test: Evaluation failure should be non-fatal."""
    with patch.object(research_service, "_run_sales_agent", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = ("# Report", {})
        with patch("src.utils.guardrails.OutputGuardrail.validate", return_value=MagicMock(is_valid=True)):
            with patch("src.agents.evaluation_service.EvaluationService.evaluate", side_effect=Exception("Eval error")):
                await research_service.process_research_background("job_123", "Acme")
                assert mock_bq_repo.update_status.called

@pytest.mark.asyncio
async def test_get_pdf_report_functional(research_service, mock_bq_repo, mock_gcs_repo):
    """Functional test for retrieving PDF report."""
    mock_bq_repo.get_status.return_value = {"status": "COMPLETED", "company_name": "Acme"}
    mock_gcs_repo.download_pdf.return_value = b"pdf_data"
    
    pdf_bytes, name = await research_service.get_pdf_report("job_123")
    assert pdf_bytes == b"pdf_data"
    assert name == "Acme"

@pytest.mark.asyncio
async def test_get_pdf_report_not_complete_functional(research_service, mock_bq_repo):
    """Functional test: get_pdf_report should raise error if job not complete."""
    mock_bq_repo.get_status.return_value = {"status": "PROCESSING"}
    with pytest.raises(ServiceError) as exc:
        await research_service.get_pdf_report("job_123")
    assert exc.value.status_code == 409
