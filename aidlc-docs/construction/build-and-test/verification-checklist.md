# Verification Checklist

- [x] Redis cache repository supports sync and async operations with 7-day TTL (`EX 604800`).
- [x] `KeywordGeneratorAgent` generates 30 queries across 12 domains using structured Pydantic output (`CandidateQueries`).
- [x] Context isolation (`include_contents="none"`) is active on all LLM agents.
- [x] `ParallelSearchAgent` executes bounded parallel searches with Redis cache-first lookup.
- [x] 12 `DOMAIN_OUTPUT_KEYS` are populated and gated via `validate_domain_outputs_present`.
- [x] `AlignmentAnalyst` maps target challenges to Colt catalog solutions.
- [x] `ReportCompiler` operates as a plain `LlmAgent` compiling the final markdown report.
- [x] `SalesResearchWorkflowAgent` coordinates the 4-phase pipeline without deprecated SequentialAgent.
- [x] BigQuery telemetry streams across all 4 tables (`research_requests`, `cost_attribution`, `agent_telemetry`, `users_feedback`).
- [x] OpenTelemetry traces distributed spans to Google Cloud Trace.
- [x] Test suite coverage passes >= 80% CI threshold.
