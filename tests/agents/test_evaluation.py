import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.agents.evaluation_service import EvaluationService

@pytest.fixture
def evaluation_service():
    return EvaluationService()

@pytest.mark.asyncio
async def test_evaluate_success(evaluation_service):
    # Mocking internal sections to avoid real LLM calls and complex metrics
    with patch.object(evaluation_service, "_run_section_a", new_callable=AsyncMock) as m1, \
         patch.object(evaluation_service, "_run_section_b", new_callable=AsyncMock) as m2:
        
        m1.return_value = {"section_a_score": 90.0, "dimensions": {}}
        m2.return_value = {"section_b_score": 80.0, "metrics": {}}
        
        result = await evaluation_service.evaluate(
            request_id="job_123",
            final_report="# Test Report",
            session_state={"raw_search_cache": []}
        )
        
        assert "final_composite_score" in result
        # (90*0.8 + 80*0.2) = 72 + 16 = 88
        assert result["final_composite_score"] == 88.0
        assert "section_a" in result
        assert "section_b" in result

@pytest.mark.asyncio
async def test_evaluate_section_a_failure(evaluation_service):
    # Test fallback when LLM call fails
    with patch.object(evaluation_service, "_call_llm_judge", new_callable=AsyncMock) as m1, \
         patch.object(evaluation_service, "_run_section_b", new_callable=AsyncMock) as m2:
        
        m1.side_effect = Exception("LLM Down")
        m2.return_value = {"section_b_score": 50.0}
        
        result = await evaluation_service.evaluate(
            request_id="job_123",
            final_report="# Test Report",
            session_state={}
        )
        assert result["section_a"]["section_a_score"] == 0.0

def test_cache_to_text(evaluation_service):
    cache = [
        {"title": "Title 1", "snippet": "Evidence 1"},
        {"title": "Title 2", "snippet": "Evidence 2"}
    ]
    text = evaluation_service._cache_to_text(cache)
    assert "Evidence 1" in text
    assert "Evidence 2" in text

def test_session_state_to_text(evaluation_service):
    state = {
        "agent_outputs": {
            "Agent1": "Output 1",
            "Agent2": "Output 2"
        }
    }
    text = evaluation_service._session_state_to_text(state)
    assert "Output 1" in text
    assert "Output 2" in text

def test_parse_and_score_section_a(evaluation_service):
    # Mocking LLM judge response
    llm_response = {
        "M12_hallucination_count": 0,
        "M13_policy_violation_count": 0,
        "scoring_rationale": {}
    }
    # Add all required dimensions for full score calculation
    from src.agents.evaluation_service import DIMENSION_CONFIG
    for dim_key in DIMENSION_CONFIG.keys():
        llm_response[dim_key] = 4 # Perfect score
            
    result = evaluation_service._parse_and_score_section_a(llm_response)
    assert "section_a_score" in result
    assert result["section_a_score"] == 100.0
    assert result["M12_hallucination_count"] == 0

def test_empty_section_a(evaluation_service):
    result = evaluation_service._empty_section_a(error="test error")
    assert result["section_a_score"] == 0.0
    assert result["error"] == "test error"

@pytest.mark.asyncio
async def test_run_section_b_minimal(evaluation_service):
    # TestSection B with minimal input
    with patch("src.agents.evaluation_service.logger"):
        # We need to mock metrics that require heavy libs
        with patch.object(evaluation_service, "_compute_rouge", return_value={"rouge1": 0.5}), \
             patch.object(evaluation_service, "_compute_groundedness", return_value=0.7), \
             patch.object(evaluation_service, "_compute_completeness", return_value=0.8), \
             patch.object(evaluation_service, "_compute_source_diversity", return_value=0.9):
            
            result = await evaluation_service._run_section_b(
                final_report="# Report",
                reference_text="Ref text",
                raw_search_cache=[]
            )
            assert "section_b_score" in result
