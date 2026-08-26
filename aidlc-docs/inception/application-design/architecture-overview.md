# Architecture Overview

```
                               SalesResearchWorkflowAgent (custom BaseAgent)
                                                    │
        ┌───────────────────────────────────────────┼───────────────────────────────────────────┐
        ▼                                           ▼                                           ▼
KeywordGeneratorAgent                      ParallelSearchAgent                        AlignmentAnalyst
(LlmAgent, output_schema=CandidateQueries) (Deterministic BaseAgent)                  (LlmAgent, output_schema=ColtAlignmentOutput)
include_contents="none"                    - Redis cache lookup (7-day TTL)           - include_contents="none"
                                           - Parallel google_search_agent             - Gemini context-cached Colt catalog
                                           - 12 DOMAIN_OUTPUT_KEYS synthesis
                                           - validate_domain_outputs_present gate
                                                    │
                                                    ▼
                                              ReportCompiler
                                              (plain LlmAgent, include_contents="none")
                                              - validate_final_report tool
                                                    │
                                                    ▼
                                            EvaluationService
                                            (Section A 80% + Section B 20% + M6 BM25)
                                                    │
                                                    ▼
                                           BigQuery Telemetry (4 Tables) & Cloud Trace
```
