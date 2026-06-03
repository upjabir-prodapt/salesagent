from pathlib import Path


def test_plan_react_callback_order_source_contract():
    path = (
        Path(__file__).resolve().parents[2]
        / "src/services/research/agents/sales/composition/leaf.py"
    )
    source = path.read_text(encoding="utf-8")

    assert "before_model_callback=[plan_before_model, before_model_callback]" in source
    assert "after_model_callback=[plan_after_model, after_model_callback]" in source
    assert "before_tool_callback=[plan_before_tool, before_tool_callback]" in source
    assert "after_tool_callback=[plan_after_tool, after_tool_callback]" in source
    assert "after_agent_callback=[plan_after_agent, after_agent_callback]" in source
