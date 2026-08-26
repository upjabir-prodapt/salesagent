# Data Models & Schemas

## 1. Candidate Queries & Query Plan (`query_generator/schemas.py`)
```python
class QueryWithMetadata(BaseModel):
    query: str
    domain: str
    year: int | None = None

class CandidateQueries(BaseModel):
    domain_queries: dict[str, list[str]]

class NormalizedQueryPlan(BaseModel):
    queries: list[QueryWithMetadata]
    total_candidates: int
    budget_used: int
    per_domain_counts: dict[str, int]
```

## 2. Redis Cache Storage Model (`redis_repository.py`)
- **Key Pattern**: `search:{company_key}:{query_hash}`
- **TTL**: `604800` seconds (7 days)
- **JSON Value**:
```json
{
  "company_name": "Acme Corp",
  "company_key": "a1b2c3d4e5f60718",
  "query": "Acme Corp revenue 2025",
  "query_hash": "9f8e7d6c5b4a3210",
  "domain": "firmographics",
  "results": [
    {
      "url": "https://example.com/sec/10k",
      "title": "Acme Corp 2025 Annual Report",
      "content": "Acme reported global revenue of $1.2B..."
    }
  ],
  "cached_at": "2026-08-25T12:00:00Z",
  "expires_at": "2026-09-01T12:00:00Z"
}
```

## 3. Colt Alignment Schema (`schemas.py`)
```python
class ColtAlignmentMapping(BaseModel):
    challenge_or_priority: str
    colt_solution: str
    alignment_justification: str

class StrategicOpportunitySummary(BaseModel):
    summary: str
    hooks: list[str]
    executive_narratives: list[str]
    regulatory_triggers: list[str]
    ai_urgency: list[str]
    competitive_displacement_angles: list[str]
    colt_differentiation: list[str]
    use_case_recommendations: list[UseCaseRecommendation]

class ColtAlignmentOutput(BaseModel):
    alignment_mappings: list[ColtAlignmentMapping]
    strategic_opportunity: StrategicOpportunitySummary
```

## 4. BigQuery Schemas (4 Tables)
1. **`research_requests`**: `job_execution_id`, `company_name`, `account_id`, `status` (`PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`), `progress`, `current_step`, `current_agent`, `gcs_uri`, `error`, `metadata`, `created_at`, `updated_at`.
2. **`cost_attribution`**: `job_execution_id`, `username`, `email`, `business_unit`, `model_version`, `input_tokens`, `output_tokens`, `total_tokens`, `search_count`, `search_cost_usd`, `token_cost_usd`, `total_cost_usd`, `latency_seconds`, `cost_usd`, `created_at`.
3. **`agent_telemetry`**: `record_id`, `job_execution_id`, `agent_name`, `agent_type`, `latency_ms`, `tokens_input`, `tokens_output`, `model_used`, `cost_usd`, `success`, `error_message`, `created_at`.
4. **`users_feedback`**: `feedback_id`, `job_execution_id`, `user_email`, `rating`, `comments`, `created_at`.
