from pathlib import Path


def test_agent_factory_callback_source_contract():
    path = Path(__file__).resolve().parents[2] / "src/worker/agents/leaf.py"
    source = path.read_text(encoding="utf-8")

    assert "before_model_callback=before_model_callback" in source
    assert "after_model_callback=after_model_callback" in source
    assert "before_tool_callback=before_tool_callback" in source
    assert "after_tool_callback=after_tool_callback" in source
    assert "before_agent_callback=before_agent_callback" in source
    assert "after_agent_callback=after_agent_callback" in source
