# Functional Design — Unit 2: KeywordGeneratorAgent with Structured Output

## 1. Overview
Generates targeted search keywords across all 12 research domains, returning a strictly typed Pydantic `CandidateQueries` object with `include_contents="none"` for complete context isolation.

## 2. Specification
- **Agent Name**: `QueryGeneratorAgent` (or `KeywordGeneratorAgent`)
- **Output Schema**: `CandidateQueries` (`domain_queries: dict[str, list[str]]`)
- **Context Isolation**: `include_contents="none"`
- **Budget**: 30 queries total across 12 domains, ranked with BM25 specificity scores and Jaccard similarity deduplication (>0.7).
- **Domain Distribution**:
  - `firmographics`: 3
  - `geographic`: 2
  - `executive`: 3
  - `strategy`: 3
  - `compliance`: 2
  - `market`: 3
  - `ecosystem`: 2
  - `tech_stack`: 3
  - `procurement`: 2
  - `growth_signals`: 2
  - `risk_signals`: 3
  - `campaign_signals`: 2
  - **Total**: 30 queries
