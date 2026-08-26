# Functional Design & Code Plan — Unit 8: Evaluation & Telemetry

## 1. Overview
Extends Section B evaluation with metric `M6_domain_evidence_groundedness` using `Bm25Verifier`, and validates BigQuery telemetry mappings for the new agent structure across all 4 tables.

## 2. Specification
- **M6 Metric**: Measures grounding of synthesized domain JSONs against raw search evidence using `Bm25Verifier`.
- **Telemetry Mapping**: `_AGENT_TYPE_MAP` updated to track `ParallelSearchAgent` alongside `QueryGeneratorAgent`, `AlignmentAnalyst`, and `ReportCompiler`.

## 3. Code Tasks
- [x] Add `compute_domain_groundedness` in `src/worker/finalization/evaluation_section_b.py`.
- [x] Update `evaluation_config.py` and `evaluation_service.py` with M6 metric.
- [x] Unit tests for evaluation and telemetry.
