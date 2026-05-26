from unittest.mock import patch

import pytest

from src.services.research.agent.evaluation_service import (
    EvaluationService,
    RESEARCH_AGENT_OUTPUT_KEYS,
)


@pytest.fixture
def evaluation_service():
    return EvaluationService()


def test_compute_agent_output_coverage(evaluation_service):
    state = {key: "data" for key in RESEARCH_AGENT_OUTPUT_KEYS.values()}
    score = evaluation_service._compute_agent_output_coverage(state)
    assert score == 1.0


def test_compute_groundedness_with_job_evidence(evaluation_service):
    report = (
        "## 12. Signals\nFact [1] is true.\n## 13. Source Summary\n[1] http://test.com"
    )
    evidence = [{"url": "http://test.com", "snippet": "Fact is true"}]
    score = evaluation_service._compute_groundedness(report, job_evidence=evidence)
    assert score >= 0


def test_compute_completeness(evaluation_service):
    report = "## 1. Company Snapshot\nData\n## 2. Global Operations\nMore data"
    score = evaluation_service._compute_completeness(report)
    assert score > 0


def test_compute_evidence_breadth(evaluation_service):
    evidence = [
        {"url": "http://a.com"},
        {"url": "http://b.com"},
        {"url": "http://c.com"},
    ]
    score = evaluation_service._compute_evidence_breadth(evidence)
    assert score > 0


@pytest.mark.asyncio
async def test_run_section_b_v2_weights(evaluation_service):
    report = "## 1. Company Snapshot\nContent here with enough text.\n" * 3
    report += "\n## 13. Source Summary\nhttp://example.com\n"
    state = {key: "ok" for key in RESEARCH_AGENT_OUTPUT_KEYS.values()}
    evidence = [{"url": "http://example.com", "snippet": "fact", "title": "t"}]

    with patch(
        "src.services.research.agent.evaluation_service.compute_semantic_groundedness",
        return_value=0.8,
    ):
        result = await evaluation_service._run_section_b(report, state, evidence)

    assert result["scoring_version"] == "v2"
    assert "M1_agent_output_coverage" in result
    assert "M5_semantic_groundedness" in result
    assert "M1_rouge1" not in result
