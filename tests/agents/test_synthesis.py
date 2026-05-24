from unittest.mock import MagicMock, patch

from google.adk.planners import PlanReActPlanner

from src.services.research.agent.sales.sub_agents.synthesis_agents import create_synthesis_agents


def test_create_synthesis_agents():
    with patch(
        "src.services.research.agent.sales.sub_agents.synthesis_agents.create_plan_react_agent"
    ) as mock_factory:
        with patch(
            "src.services.research.agent.sales.sub_agents.synthesis_agents.make_report_verification_agent_tool"
        ) as mock_verify_tool:
            mock_verify_tool.return_value = MagicMock(name="ReportVerificationAgent")

            def mock_agent(name, *args, **kwargs):
                m = MagicMock(name=name)
                m.name = name
                m.planner = PlanReActPlanner() if name == "ReportCompiler" else None
                m.tools = kwargs.get("extra_tools") or []
                return m

            mock_factory.side_effect = mock_agent
            alignment, compiler = create_synthesis_agents()

            assert alignment.name == "AlignmentAnalyst"
            assert compiler.name == "ReportCompiler"

            compiler_call = [
                c for c in mock_factory.call_args_list if c.kwargs.get("name") == "ReportCompiler"
            ][0]
            assert compiler_call.kwargs["include_web_search"] is False
            assert compiler_call.kwargs["include_bm25_verify"] is False
            assert compiler_call.kwargs["output_key"] == "final_report"
            mock_verify_tool.assert_called_once()
