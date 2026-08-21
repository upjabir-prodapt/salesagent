# Quick Reference: New Architecture

## At a Glance

| Aspect | Before | After |
|--------|--------|-------|
| **Query Generation** | 12 agents in parallel | 1 unified agent |
| **Query Count** | Dynamic per agent | Fixed: max 40 (BM25 selected) |
| **Search Dedup** | Per-agent logic | Jaccard similarity (>0.7) |
| **Alignment Context** | Vector search + catalog | PDF + hardcoded fallback |
| **Search Caching** | None | BigQuery by company_name + query |
| **Cost Tracking** | Tokens only | Tokens + searches + breakdown |

## Code Locations

### Adding a New Query Generator Feature
```
src/services/research/agents/sales/query_generator/
├── schemas.py        ← Add new data class here
├── prompt.py         ← Modify prompt guidance
├── bm25_selector.py  ← Adjust DOMAIN_LIMITS or scoring
└── factory.py        ← Integrate new component
```

### Adding Search Cache Features
```
src/services/research/search_cache/service.py
├── def get_cached_searches()      ← Add query type
├── def cache_search_results()     ← Add storage logic
└── def get_uncached_queries()     ← Add filtering
```

### Adding Cost Pricing
```
src/services/research/cost/analyzer.py
├── _parse_pricing()               ← Add model pricing
├── calculate_token_cost()         ← Update token math
└── calculate_search_cost()        ← Update search math
```

### Updating Alignment
```
src/services/research/agents/sales/tools/
├── gcs_pdf_loader.py              ← GCS / fallback logic
├── alignment_context.py           ← Tool creation
└── ../prompts/synthesis_alignment_prompts.py  ← Prompt
```

## Common Tasks

### 1. Add a New Research Domain

1. **Update Query Generator Prompt**
   ```python
   # src/services/research/agents/sales/query_generator/prompt.py
   def build_query_generator_prompt(...):
       domains_str = ", ".join(domains)  # Automatically includes new domain
   ```

2. **Update BM25 Limits**
   ```python
   # src/services/research/agents/sales/query_generator/bm25_selector.py
   DOMAIN_LIMITS = {
       "my_new_domain": 3,  # Add this line
       ...
   }
   ```

### 2. Adjust Query Budget (Max 40)

```python
# src/services/research/agents/sales/query_generator/bm25_selector.py
class Bm25QuerySelector:
    TOTAL_BUDGET = 50  # Change from 40 to 50
    DOMAIN_LIMITS = {
        "firmographics": 5,  # Increase from 4
        ...
    }
```

### 3. Tune BM25 Scoring

```python
# src/services/research/agents/sales/query_generator/bm25_selector.py
def _compute_bm25_score(self, query: str, company_name: str) -> float:
    score = 0.0
    
    # Adjust these weights:
    if company_name.lower() in query.lower():
        score += 3.0  # Was 2.0, increase company name boost
    
    domain_keywords = {
        "revenue": 2.0,  # Was 1.5, weight specific keywords more
        ...
    }
```

### 4. Change Deduplication Threshold

```python
# src/services/research/agents/sales/query_generator/bm25_selector.py
def _deduplicate_queries(self, queries: list[QueryWithMetadata]):
    for seen in seen_terms:
        jaccard = intersection / union if union > 0 else 0
        if jaccard > 0.8:  # Was 0.7, stricter dedup
            is_duplicate = True
```

### 5. Update Search Pricing

```bash
# .env
GOOGLE_SEARCH_PRICING_3X=15.0  # Was 14.0
GOOGLE_SEARCH_PRICING_2X=40.0  # Was 35.0
```

### 6. Add PDF Context Fallback

```python
# src/services/research/agents/sales/tools/gcs_pdf_loader.py
COLT_CATALOG_HARDCODED = """
Add new PDF text here...
"""
```

### 7. Check Search Cache Hit Rate

```python
# In your monitoring script
from src.services.research.search_cache import SearchCacheService

cache = SearchCacheService()
total = cache.get_search_count("Acme Corp")
print(f"Search count for Acme Corp: {total}")
```

### 8. Clear Cache for a Company

```python
# Direct BigQuery
DELETE FROM `project.dataset.search_cache`
WHERE company_name = "Acme Corp";
```

## File Dependencies

```
app.py
  ├─ QueryGeneratorFactory
  │   └─ Bm25QuerySelector
  │       └─ CandidateQueries
  ├─ SearchOrchestrator
  │   ├─ SearchCacheService
  │   │   └─ BigQueryRepository
  │   └─ CostAnalyzer
  └─ AlignmentAnalyst
      ├─ GCS PDF loader
      └─ Alignment context tool
```

## Debugging Checklist

### Queries not generated?
- [ ] Check QueryGeneratorAgent prompt in `query_generator/prompt.py`
- [ ] Verify JSON parsing in `factory.py:_parse_candidate_queries()`
- [ ] Check LLM output format (must be valid JSON)

### Cache misses high?
- [ ] Check deduplication threshold (currently 0.7)
- [ ] Verify query_hash calculation in `SearchCacheService`
- [ ] Check BigQuery `search_cache` table has data

### Costs not calculated?
- [ ] Verify env vars `GOOGLE_SEARCH_PRICING_3X` and `GOOGLE_SEARCH_PRICING_2X`
- [ ] Check model version detection in `CostAnalyzer._extract_model_version()`
- [ ] Verify search_count is being tracked

### Alignment context not loading?
- [ ] Check GCS path: `{bucket}/{parent}/alignment_context/{company}_catalog.pdf`
- [ ] Verify fallback hardcoded text in `gcs_pdf_loader.py`
- [ ] Check alignment tool is registered in synthesis agents

## Performance Metrics

Track these KPIs:
- **Query generation time:** Should be < 30s (was 2-3 min with 12 agents)
- **Unique queries generated:** Should be 35-40 (after BM25)
- **Cache hit rate:** Should improve over time (target 70%+ after 5+ companies)
- **Total cost per research:** Should be 20-25% lower than before
- **End-to-end latency:** Should be 10-15% faster

## Rollback Instructions

If something breaks:

```bash
# 1. Revert app.py to old composition
git checkout HEAD~1 src/services/research/agents/sales/composition/app.py

# 2. Revert synthesis.py to remove alignment context
git checkout HEAD~1 src/services/research/agents/sales/composition/synthesis.py

# 3. Restart service
# (depends on your deployment)
```

**Note:** Cost analysis and search cache can stay—they're useful for other features.

## Useful Queries

### See top domains by query count
```sql
SELECT 
  domain,
  COUNT(*) as count
FROM `project.dataset.search_cache`
WHERE company_name = "Acme Corp"
GROUP BY domain
ORDER BY count DESC;
```

### See search cost per company
```sql
SELECT 
  company_name,
  COUNT(*) as searches,
  SUM(CASE WHEN search_cost_usd IS NOT NULL THEN search_cost_usd ELSE 0 END) as total_cost
FROM `project.dataset.cost_attribution`
GROUP BY company_name
ORDER BY total_cost DESC;
```

### Find duplicate searches (dedup failures)
```sql
SELECT 
  query_hash,
  COUNT(DISTINCT query) as unique_queries
FROM `project.dataset.search_cache`
GROUP BY query_hash
HAVING COUNT(DISTINCT query) > 1;
```

## Config Examples

### Conservative Budget (25 queries)
```python
DOMAIN_LIMITS = {
    "firmographics": 2,
    "geographic": 2,
    "executive": 2,
    "strategy": 2,
    "compliance": 2,
    "market": 2,
    "ecosystem": 2,
    "tech_stack": 2,
    "procurement": 1,
    "growth_signals": 2,
    "risk_signals": 2,
    "campaign_signals": 1,
}
TOTAL_BUDGET = 25
```

### Aggressive Budget (60 queries)
```python
DOMAIN_LIMITS = {
    "firmographics": 6,
    "geographic": 5,
    "executive": 6,
    "strategy": 5,
    "compliance": 5,
    "market": 5,
    "ecosystem": 5,
    "tech_stack": 5,
    "procurement": 3,
    "growth_signals": 5,
    "risk_signals": 5,
    "campaign_signals": 4,
}
TOTAL_BUDGET = 60
```

## Contact & Support

For questions about:
- **Query generation:** See `src/services/research/agents/sales/query_generator/`
- **Caching:** See `src/services/research/search_cache/`
- **Cost analysis:** See `src/services/research/cost/`
- **Alignment:** See `src/services/research/agents/sales/tools/`
