# AI-DLC Audit Trail

## Inception Phase — Workspace Detection & Architecture Review
**Timestamp**: 2026-08-25T10:00:00Z
**User Input**: "could you explain this repo, particulary how agents define, how much agents are there and its working etc"
**AI Action**: Analyzed the full repository (`src/api`, `src/worker`, `src/shared`), agent definitions in ADK, and telemetry/BigQuery infrastructure.
**Decision**: Identified 4 active ADK agents in the sequential pipeline + 1 nested search sub-agent + post-run EvaluationService.

---

## Inception Phase — Requirements & Pipeline Restructuring Plan
**Timestamp**: 2026-08-25T10:15:00Z
**User Input**: Plan for single keyword agent (30 keywords across 12 domains), parallel web search, Redis caching (7-day TTL), context-cached Colt catalog alignment, plain compiler agent, and evaluation reuse.
**AI Action**: Formulated Inception plan covering functional/non-functional requirements, Redis vs Firestore options, BM25 keyword budget, and evaluation extension.
**Decision**: User selected Redis / Cloud Memorystore for 7-day caching with native TTL.

---

## Inception Phase — Custom Workflow Agent & Context Isolation Refinement
**Timestamp**: 2026-08-25T10:30:00Z
**User Input**: "report compiler needs to normal agent rather than planreact agent. Also if possible to google adk custom workflow agent ... context of first agent doesnt need to send 2nd agent , only output needs to send, similarly for alignment analyst. you need to keep track of cost of each agent in the sense of input token, output token, etc You need to push into biq query as well ... four biq query ... traces on cloud traces ... update adk to latest"
**AI Action**:
1. Upgraded ADK dependency floor to `>=2.1.0`.
2. Designed `SalesResearchWorkflowAgent` (custom `BaseAgent` subclass) with manual `_run_async_impl` orchestrating `KeywordGeneratorAgent` -> `ParallelSearchAgent` -> `AlignmentAnalyst` -> `ReportCompiler`.
3. Set `include_contents="none"` on all LLM sub-agents to enforce strict context isolation (only declared output keys injected).
4. Reused existing per-agent token/cost telemetry in `run/telemetry.py` to stream into the 4 BigQuery tables (`agent_telemetry`, `cost_attribution`, `research_requests`, `users_feedback`).
5. Reused OpenTelemetry / Cloud Trace spans.
6. Approved by user for Construction.
