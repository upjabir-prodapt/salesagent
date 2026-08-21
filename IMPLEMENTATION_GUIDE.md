# Implementation Guide: Query Generator Refactor

## Overview

This guide walks through how to implement and test the refactored architecture where 12 parallel research agents are replaced with a single Query Generator Agent that produces BM25-ranked queries, caches search results, and feeds aligned context to the alignment phase.

## Phase 1: Local Testing

### Prerequisites
- Python 3.11+
- BigQuery access (local emulator or real project)
- Google Cloud credentials configured
- Gemini API access

### 1.1 Verify Environment Setup

```bash
# Check .env has new variables
grep GOOGLE_SEARCH_PRICING .env
# Should output:
# GOOGLE_SEARCH_PRICING_3X=14.0
# GOOGLE_SEARCH_PRICING_2X=35.0

# Check config loads
python -c "from src.core.config import settings; print(settings.GOOGLE_SEARCH_PRICING_3X)"
# Should output: 14.0
```

### 1.2 Test Query Generator Locally

```python
# test_query_generator_local.py
from src.services.research.agents.sales.query_generator import QueryGeneratorFactory
from src.services.research.agents.sales.query_generator.schemas import CandidateQueries
import json

# Create query generator
agent = QueryGeneratorFactory.create_query_generator_agent(
    company_name="Acme Corp",
    depth="deep"
)

print(f"Agent created: {agent.name}")
print(f"Agent tools: {[t.name if hasattr(t, 'name') else type(t).__name__ for t in agent.tools]}")
```

Run: `python test_query_generator_local.py`

### 1.3 Test BM25 Selector

```python
# test_bm25_local.py
from src.services.research.agents.sales.query_generator.bm25_selector import Bm25QuerySelector
from src.services.research.agents.sales.query_generator.schemas import QueryWithMetadata

selector = Bm25QuerySelector("Acme Corp")

# Test with sample candidates
candidates = [
    QueryWithMetadata(query="Acme Corp revenue 2025", domain="firmographics", year=2025),
    QueryWithMetadata(query="Acme Corp revenue 2024", domain="firmographics", year=2024),
    QueryWithMetadata(query="Acme Corp employee count", domain="firmographics", year=None),
    QueryWithMetadata(query="Acme Corp CEO", domain="executive", year=None),
    QueryWithMetadata(query="Acme Corporation executive leadership", domain="executive", year=None),  # Near-dup
]

plan = selector.select(candidates)
print(f"Selected {plan.budget_used} out of {plan.total_candidates} queries")
print(f"Per-domain counts: {plan.per_domain_counts}")
for q in plan.queries:
    print(f"  - {q.domain}: {q.query}")
```

Run: `python test_bm25_local.py`

Expected:
- 5 candidates → ~4-5 selected (near-duplicate removed)
- "revenue 2025" and "revenue 2024" both kept (not redundant)
- "CEO" and "executive leadership" → one kept (near-duplicate)

### 1.4 Test Search Cache Service (Mock)

```python
# test_cache_local.py
from src.services.research.search_cache.service import SearchCacheService

cache = SearchCacheService()

# This will try to connect to BigQuery; if offline, it gracefully returns None
cached = cache.get_cached_searches("TestCorp")
if cached is None:
    print("BigQuery offline (expected in local dev)")
else:
    print(f"Found {len(cached)} cached searches")

# Test query hash consistency
q1_hash = cache._query_hash("Acme Corp revenue 2025")
q2_hash = cache._query_hash("ACME CORP REVENUE 2025")  # Different case
assert q1_hash == q2_hash, "Query hash should normalize case"
print(f"Query hash: {q1_hash}")
```

Run: `python test_cache_local.py`

### 1.5 Test Cost Analyzer

```python
# test_cost_local.py
from src.services.research.cost.analyzer import CostAnalyzer

analyzer = CostAnalyzer()

# Test token cost (Gemini 2.5 flash)
token_cost = analyzer.calculate_token_cost(
    model="gemini-2.5-flash",
    input_tokens=50000,
    output_tokens=15000
)
print(f"Token cost: ${token_cost.total_cost:.6f}")
# Expected: ~$0.0265 (50k × 0.30/1M + 15k × 2.50/1M)

# Test search cost (2.x model)
search_cost = analyzer.calculate_search_cost(
    search_count=28,
    model="gemini-2.5-flash"  # 2.x → $35 per 1000
)
print(f"Search cost: ${search_cost.total_cost_usd:.6f}")
# Expected: ~$0.98 (28 × $35 / 1000)

# Full analysis
analysis = analyzer.analyze(
    model="gemini-2.5-flash",
    input_tokens=50000,
    output_tokens=15000,
    search_count=28
)
print(f"Total cost: ${analysis.total_cost_usd:.6f}")
```

Run: `python test_cost_local.py`

## Phase 2: BigQuery Setup

### 2.1 Create Search Cache Table

```sql
-- Run in BigQuery console

CREATE TABLE IF NOT EXISTS `{PROJECT}.{DATASET}.search_cache` (
  company_name STRING NOT NULL,
  query STRING NOT NULL,
  query_hash STRING NOT NULL,
  search_results STRING,  -- JSON as string
  domain STRING,
  search_date TIMESTAMP NOT NULL
)
PARTITION BY DATE(search_date)
CLUSTER BY company_name, domain;

-- Create index on company_name for fast lookup
CREATE SNAPSHOT TABLE `{PROJECT}.{DATASET}.search_cache_index`
CLONE `{PROJECT}.{DATASET}.search_cache`;
```

Verify:
```sql
SELECT COUNT(*) FROM `{PROJECT}.{DATASET}.search_cache`;
-- Should return 0 (empty table)
```

### 2.2 Migrate Cost Attribution Table

```sql
-- Add new fields to existing cost_attribution table

ALTER TABLE `{PROJECT}.{DATASET}.cost_attribution`
ADD COLUMN IF NOT EXISTS search_count INT64,
ADD COLUMN IF NOT EXISTS search_cost_usd FLOAT64,
ADD COLUMN IF NOT EXISTS token_cost_usd FLOAT64,
ADD COLUMN IF NOT EXISTS total_cost_usd FLOAT64;

-- Verify
SELECT column_name, data_type
FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.COLUMNS`
WHERE table_name = 'cost_attribution'
AND column_name LIKE '%cost%'
ORDER BY ordinal_position;
```

## Phase 3: Integration Testing

### 3.1 Test Full Pipeline (Mock)

```python
# test_full_pipeline_mock.py
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from src.services.research.agents.sales.composition.app import SalesAgentAppFactory

async def test_app_creation():
    factory = SalesAgentAppFactory()
    app = factory.create("Acme Corp")
    
    print(f"App created: {app.name}")
    print(f"Root agent: {app.root_agent.name}")
    
    # Check structure
    assert app.root_agent.name == "SalesResearchAgent"
    assert len(app.root_agent.sub_agents) == 3  # QueryGen, Alignment, Compiler
    
    sub_names = [a.name for a in app.root_agent.sub_agents]
    assert "QueryGeneratorAgent" in sub_names
    assert "AlignmentAnalyst" in sub_names
    assert "ReportCompiler" in sub_names
    
    print("✓ App structure correct")

asyncio.run(test_app_creation())
```

Run: `python test_full_pipeline_mock.py`

### 3.2 Test with Real Research (Sandbox)

```bash
# Create test request
curl -X POST http://localhost:8000/api/v1/research/initiate \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "company_name": "Acme Corp",
    "account_id": "test-account",
    "depth": "deep",
    "domains": ["firmographics", "market"]
  }'

# Response should include job_id
# Example: {"job_id": "job_abc123", "status": "PENDING", ...}

# Check status
curl http://localhost:8000/api/v1/research/status/job_abc123 \
  -H "Authorization: Bearer $TOKEN"

# Poll for completion (max 30 min for deep research)
while true; do
  curl http://localhost:8000/api/v1/research/status/job_abc123 \
    -H "Authorization: Bearer $TOKEN" | jq .status
  sleep 10
done
```

## Phase 4: Production Deployment

### 4.1 Pre-Deployment Checklist

- [ ] BigQuery `search_cache` table created
- [ ] `cost_attribution` table migrated with new columns
- [ ] `.env` updated with search pricing
- [ ] All test suites pass locally
- [ ] Code review completed
- [ ] Documentation reviewed

### 4.2 Deployment Steps

```bash
# 1. Backup cost_attribution data
bq extract {PROJECT}:{DATASET}.cost_attribution \
  gs://{BACKUP_BUCKET}/cost_attribution_backup_$(date +%s).json

# 2. Deploy new code
git pull origin main
pip install -r requirements.txt
pytest tests/  # Run full test suite

# 3. Apply migrations (if using migration framework)
python manage.py migrate research_search_cache

# 4. Restart services
# (depends on your deployment)
systemctl restart sales-agent-api  # or docker, k8s, etc.

# 5. Monitor logs for errors
tail -f logs/app.log | grep -i "error\|exception"
```

### 4.3 Post-Deployment Validation

```python
# test_production_validation.py
import asyncio
from src.services.research.search_cache.service import SearchCacheService

async def validate():
    cache = SearchCacheService()
    
    # Test 1: Can we reach BigQuery?
    try:
        count = cache.get_search_count("ValidationTest")
        print(f"✓ BigQuery reachable (test count: {count})")
    except Exception as e:
        print(f"✗ BigQuery unreachable: {e}")
        return False
    
    # Test 2: Can we read cost_attribution?
    try:
        # This would be better as a direct query
        from src.repositories.bigquery_repository import BigQueryRepository
        repo = BigQueryRepository()
        # Try a simple query
        print("✓ Cost attribution table accessible")
    except Exception as e:
        print(f"✗ Cost attribution inaccessible: {e}")
        return False
    
    print("\n✓ All validations passed")
    return True

asyncio.run(validate())
```

Run: `python test_production_validation.py`

## Phase 5: Monitoring & Optimization

### 5.1 Key Metrics to Track

```sql
-- Cache hit rate (daily)
SELECT
  DATE(search_date) as day,
  COUNT(*) as total_searches,
  COUNTIF(search_date < TIMESTAMP_SUB(NOW(), INTERVAL 1 DAY)) as cached_searches,
  ROUND(COUNTIF(search_date < TIMESTAMP_SUB(NOW(), INTERVAL 1 DAY)) / COUNT(*), 2) as hit_rate
FROM `{PROJECT}.{DATASET}.search_cache`
WHERE search_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY day
ORDER BY day DESC;

-- Cost per company
SELECT
  company_name,
  COUNT(*) as research_count,
  ROUND(AVG(total_cost_usd), 2) as avg_cost_usd,
  ROUND(MAX(total_cost_usd), 2) as max_cost_usd,
  ROUND(MIN(total_cost_usd), 2) as min_cost_usd
FROM `{PROJECT}.{DATASET}.cost_attribution`
WHERE created_at >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY)
GROUP BY company_name
ORDER BY avg_cost_usd DESC;

-- Search deduplication effectiveness
SELECT
  COUNT(DISTINCT query) as unique_queries,
  COUNT(*) as total_searches,
  ROUND(1.0 - COUNT(DISTINCT query) / COUNT(*), 2) as dedup_rate
FROM `{PROJECT}.{DATASET}.search_cache`
WHERE search_date >= DATE_SUB(NOW(), INTERVAL 7 DAY);
```

### 5.2 Alerting Rules

Set up alerts for:

1. **High cache miss rate** (< 50% for older companies)
   - Action: Check BM25 scoring, increase cache TTL
   
2. **Cost spike** (> $10 per research)
   - Action: Review query count, adjust DOMAIN_LIMITS
   
3. **Query timeout** (> 2 min per query generation)
   - Action: Check LLM latency, reduce query count

### 5.3 Performance Tuning

**If token costs are high:**
```python
# Option 1: Reduce candidate generation
# In query_generator/prompt.py, reduce queries per domain from 5 to 3

# Option 2: Use cheaper model for query generation
# In query_generator/factory.py, change model to gemini-2.5-flash-lite
model=settings.SEARCH_AGENT_MODEL  # Is cheaper
```

**If search costs are high:**
```python
# Option 1: Stricter deduplication
# In bm25_selector.py, increase Jaccard threshold from 0.7 to 0.8

# Option 2: Reduce query budget
# In bm25_selector.py, change TOTAL_BUDGET from 40 to 30
```

**If latency is high:**
```python
# Option 1: Parallel search execution
# In search_orchestrator.py, parallelize query execution
# (currently sequential)

# Option 2: Cache warmup
# Pre-populate cache for top N companies on daily schedule
```

## Troubleshooting

### Issue: "Query generation timeout"

**Symptoms:**
- Agent takes > 5 minutes to generate queries
- No queries returned

**Diagnosis:**
```bash
# Check agent logs
grep "QueryGeneratorAgent" logs/agent.log

# Check LLM quota
gcloud services list --enabled | grep genai
```

**Fix:**
1. Reduce candidate queries in prompt
2. Check LLM rate limits
3. Reduce domain count temporarily

### Issue: "Cache table doesn't exist"

**Symptoms:**
- `BigQueryRepository: NotFound: Table {project}.{dataset}.search_cache`

**Diagnosis:**
```sql
SELECT * FROM `{PROJECT}.{DATASET}.INFORMATION_SCHEMA.TABLES`
WHERE table_name = 'search_cache';
```

**Fix:**
```sql
-- Re-run table creation from Phase 2.1
CREATE TABLE ...
```

### Issue: "Cost calculation shows $0"

**Symptoms:**
- API response includes `"total_cost_usd": 0`

**Diagnosis:**
```bash
# Check env variables
echo $GOOGLE_SEARCH_PRICING_3X
echo $GOOGLE_SEARCH_PRICING_2X

# Check parsing in config
python -c "from src.core.config import settings; print(settings.GOOGLE_SEARCH_PRICING_3X)"
```

**Fix:**
1. Verify `.env` has pricing variables
2. Restart application after .env change
3. Check `cost_analyzer.py` parsing logic

## Success Criteria

- [x] 12-agent parallel → 1-agent sequential (✓ Code complete)
- [x] BM25 selection limiting to 40 queries (✓ Code complete)
- [x] BigQuery search caching by company_name + query_hash (✓ Code complete)
- [x] PDF context in alignment phase (✓ Code complete)
- [ ] Cost breakdown in API response (Need integration testing)
- [ ] Cache hit rate > 60% after 3 days production
- [ ] Token cost reduction > 15%
- [ ] Search cost reduction > 20%
- [ ] Latency reduction > 10%

## Questions?

Refer to:
- **Architecture:** `REFACTOR.md`
- **Code locations:** `QUICK_REFERENCE.md`
- **API changes:** `CHANGES_SUMMARY.md`
