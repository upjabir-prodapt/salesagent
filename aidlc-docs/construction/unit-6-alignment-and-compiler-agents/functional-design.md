# Functional Design & Code Plan — Unit 6: AlignmentAnalyst & ReportCompiler

## 1. Overview
Replaces PlanReAct compiler with a plain `LlmAgent` and configures `AlignmentAnalyst` with context isolation (`include_contents="none"`), structured output (`ColtAlignmentOutput`), and context-cached catalog loading.

## 2. Specification
- **AlignmentAnalyst**:
  - `include_contents="none"`
  - `output_schema=ColtAlignmentOutput`
  - Injected keys: 12 domain outputs + `retrieve_alignment_context()`
- **ReportCompiler**:
  - `include_contents="none"`
  - Plain `LlmAgent` (no `PlanReActPlanner`)
  - Tool: `[validate_final_report_tool]`
  - Output Key: `final_report`

## 3. Code Tasks
- [x] Update `src/worker/agents/sales/prompts/synthesis_alignment_prompts.py`.
- [x] Update `src/worker/agents/sales/prompts/synthesis_report_prompts.py`.
- [x] Update `src/worker/agents/sales/composition/synthesis.py`.
