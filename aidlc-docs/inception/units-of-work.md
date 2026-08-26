# Units of Work

## Build Sequence
1. **Unit 1**: Redis Search Cache Repository (`src/shared/repositories/redis_repository.py`) & Backend Adapter (`src/worker/search_cache/service.py`)
2. **Unit 2**: KeywordGeneratorAgent with Structured Output (`src/worker/agents/sales/query_generator/`)
3. **Unit 3**: ParallelSearchAgent with Redis Cache & Bounded Concurrency (`src/worker/agents/sales/composition/parallel_search_agent.py`)
4. **Unit 4**: Deterministic Domain Synthesis & Domain Contracts (`src/worker/domain/agent_contracts.py`, `src/worker/agents/sales/tools/domain_outputs.py`)
5. **Unit 5**: Colt Catalog Context Caching Wrapper (`src/worker/agents/sales/tools/gcs_pdf_loader.py`)
6. **Unit 6**: AlignmentAnalyst & ReportCompiler as Plain LlmAgents (`src/worker/agents/sales/composition/synthesis.py`, `src/worker/agents/sales/prompts/synthesis_alignment_prompts.py`)
7. **Unit 7**: SalesResearchWorkflowAgent Custom ADK Workflow & Graph Rewire (`src/worker/agents/sales/composition/sales_workflow_agent.py`, `src/worker/agents/sales/composition/app.py`)
8. **Unit 8**: Extended Evaluation (M6 Domain Groundedness) & Telemetry Map (`src/worker/finalization/`, `src/worker/run/telemetry.py`)
9. **Unit 9**: Build & Test Verification across all suites
