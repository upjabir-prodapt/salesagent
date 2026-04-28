import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from src.agents.evaluation_service import EvaluationService

@pytest.fixture
def evaluation_service():
    return EvaluationService()

def test_compute_rouge(evaluation_service):
    # Patch the external library used inside the method
    with patch("rouge_score.rouge_scorer.RougeScorer") as mock_scorer_cls:
        mock_scorer = mock_scorer_cls.return_value
        mock_scorer.score.return_value = {
            "rouge1": MagicMock(fmeasure=0.5),
            "rouge2": MagicMock(fmeasure=0.4),
            "rougeLsum": MagicMock(fmeasure=0.3)
        }
        scores = evaluation_service._compute_rouge("report", "reference")
        assert scores["rouge1"] == 0.5
@pytest.mark.asyncio
async def test_compute_bertscore(evaluation_service):
    # Patch the library imported inside the method
    with patch("bert_score.score") as mock_score_fn:
        # bert_score.score returns (P, R, F1)
        f1_mock = MagicMock()
        # Ensure F1[0] returns something that can be float() converted
        f1_mock.__getitem__.return_value = 0.8
        # Ensure F1.mean().item() also returns something that can be float() converted
        f1_mock.mean.return_value.item.return_value = 0.8
        
        mock_score_fn.return_value = (None, None, f1_mock)
        
        # evaluation_service._compute_bertscore is synchronous but we run it in a thread
        score = await asyncio.to_thread(evaluation_service._compute_bertscore, "report", "reference")
        # Ensure we check against float
        assert abs(float(score) - 0.8) < 1e-6


import asyncio

def test_compute_groundedness(evaluation_service):
    report = "## 12. Signals\nFact [1] is true.\n## 13. Source Summary\n[1] http://test.com"
    cache = [{"url": "http://test.com", "snippet": "Fact is true"}]
    score = evaluation_service._compute_groundedness(report, cache)
    assert score >= 0

def test_compute_completeness(evaluation_service):
    report = "## 1. Company Snapshot\nData\n## 2. Global Operations\nMore data"
    score = evaluation_service._compute_completeness(report)
    assert score > 0

def test_compute_source_diversity(evaluation_service):
    report = "Source [1](http://a.com), [2](http://b.com)"
    cache = [{"url": "http://a.com"}, {"url": "http://b.com"}]
    score = evaluation_service._compute_source_diversity(report, cache)
    assert score > 0
