# Functional Design & Code Plan — Unit 7: SalesResearchWorkflowAgent

## 1. Overview
Replaces deprecated `SequentialAgent` with a custom ADK `BaseAgent` (`SalesResearchWorkflowAgent`) that explicitly orchestrates the 4 sub-agents:
1. `QueryGeneratorAgent`
2. `ParallelSearchAgent`
3. `AlignmentAnalyst`
4. `ReportCompiler`

## 2. Context Isolation
All LLM sub-agents operate with `include_contents="none"`. Information flows strictly through explicit state keys (`query_generator_output` -> `DOMAIN_OUTPUT_KEYS` -> `alignment_output` -> `final_report`).

## 3. Code Tasks
- [x] Create `src/worker/agents/sales/composition/sales_workflow_agent.py`.
- [x] Update `src/worker/agents/sales/composition/app.py` to use `SalesResearchWorkflowAgent`.
- [x] Update `src/worker/domain/agent_contracts.py` to register `ParallelSearchAgent`.
