from src.worker.domain.agent_contracts import (
    AGENT_CONTRACTS,
    AGENT_OUTPUT_KEYS,
    get_agent_contract,
)
from src.worker.domain.session_state import ResearchSessionState


def test_agent_contract_registry_matches_legacy_output_keys() -> None:
    assert len(AGENT_CONTRACTS) == len(AGENT_OUTPUT_KEYS)
    assert get_agent_contract("ReportCompiler") is not None
    assert AGENT_OUTPUT_KEYS["ReportCompiler"] == "final_report"


def test_research_session_state_exposes_tracked_outputs() -> None:
    raw_state = {
        "final_report": "# Final report",
        "query_generator_output": "Query generator findings",
        "empty_output": "",
    }
    state = ResearchSessionState(raw_state)

    tracked = state.tracked_outputs()

    assert tracked["ReportCompiler"] == "# Final report"
    assert tracked["QueryGeneratorAgent"] == "Query generator findings"
