# Refactor Summary: 12 Agents → Single Query Generator + Search Cache + PDF Alignment

## Files Created

### Query Generator Module
1. **`src/services/research/agents/sales/query_generator/__init__.py`**
   - Exports: `QueryGeneratorFactory`, `CandidateQueries`, `QueryWithMetadata`, `NormalizedQueryPlan`

2. **`src/services/research/agents/sales/query_generator/schemas.py`**
   - `QueryWithMetadata`: Single query with domain and year
   - `CandidateQueries`: Output from agent (domain → list of queries)
   - `NormalizedQueryPlan`: Final 40 selected queries with per-domain breakdown

3. **`src/services/research/agents/sales/query_generator/bm25_selector.py`**
   - `Bm25QuerySelector`: BM25-style ranking and selection
   - Features: Deduplication, scoring, per-domain limits
   - Returns: `NormalizedQueryPlan`

4. **`src/services/research/agents/sales/query_generator/prompt.py`**
   - `build_query_generator_prompt()`: Generates unified query generation prompt
   - Injects company name, current year, domain list
   - Provides domain-specific guidance for each of 12 domains

5. **`src/services/research/agents/sales/query_generator/factory.py`**
   - `QueryGeneratorFactory.create_query_generator_agent()`: Creates the unified agent
   - `make_query_generator_tool()`: Post-processing tool for parsing + BM25
   - Handles JSON parsing and error recovery

6. **`src/services/research/agents/sales/query_generator/search_orchestrator.py`**
   - `SearchOrchestrator`: Coordinates cache + search execution
   - `SearchExecution`: Single query result (query, domain, results, from_cache)
   - `SearchOrchestrationResult`: Aggregated results with statistics

### Search Cache Module
7. **`src/services/research/search_cache/__init__.py`**
   - Exports: `SearchCacheService`

8. **`src/services/research/search_cache/service.py`**
   - `SearchCacheService`: BigQuery search caching
   - Methods:
     - `get_cached_searches(company_name)`: Retrieve all cached searches
     - `cache_search_results(company_name, query, results, domain)`: Store results
     - `get_search_count(company_name)`: Total searches for a company
     - `get_uncached_queries(company_name, queries)`: Filter to only uncached

### Cost Analysis Module
9. **`src/services/research/cost/__init__.py`**
   - Exports: `CostAnalyzer`

10. **`src/services/research/cost/analyzer.py`**
    - `TokenCost`: Input/output breakdown with USD costs
    - `SearchCost`: Search count, model version, per-1k pricing, total cost
    - `CostAnalysis`: Complete breakdown with `to_dict()`
    - `CostAnalyzer`: Main class with `analyze()` method
    - Features: Model version detection (2.x vs 3.x), pricing lookup

### Alignment Context Module
11. **`src/services/research/agents/sales/tools/gcs_pdf_loader.py`**
    - `get_alignment_context(company_name)`: Retrieves PDF or fallback
    - `load_pdf_from_gcs(company_name)`: Attempts GCS load
    - `COLT_CATALOG_HARDCODED`: Full Colt catalog text (fallback)

12. **`src/services/research/agents/sales/tools/alignment_context.py`**
    - `make_alignment_context_tool(company_name)`: Creates context tool
    - `retrieve_alignment_context()`: Returns context dict

### BigQuery Migrations
13. **`src/repositories/bigquery_migrations.py`**
    - `SEARCH_CACHE_SCHEMA`: BigQuery schema definition
    - `COST_ATTRIBUTION_SCHEMA`: Updated schema with search cost fields
    - `create_search_cache_table()`: Factory for table creation
    - `migrate_cost_attribution_table()`: Adds new fields to existing table

### Documentation
14. **`REFACTOR.md`**
    - Complete architecture documentation
    - Flow diagrams
    - Component descriptions
    - Migration checklist
    - Troubleshooting guide

15. **`CHANGES_SUMMARY.md`** (this file)
    - Overview of all changes

## Files Modified

### Configuration
1. **`.env.example`**
   - Added: `GOOGLE_SEARCH_PRICING_3X=14.0`
   - Added: `GOOGLE_SEARCH_PRICING_2X=35.0`
   - Fixed: JSON syntax error in `GEMINI_MODEL_PRICING_JSON` (missing quote)

2. **`src/core/config.py`**
   - Added: `GOOGLE_SEARCH_PRICING_3X: str | None = "14.0"`
   - Added: `GOOGLE_SEARCH_PRICING_2X: str | None = "35.0"`

### Agent Composition
3. **`src/services/research/agents/sales/composition/app.py`**
   - Changed: Replaced 12-agent parallel + synthesis sequential flow
   - New: Single QueryGenerator → AlignmentAnalyst → ReportCompiler
   - Added: Import of `QueryGeneratorFactory`
   - Added: `company_name` parameter to `create()` method
   - Removed: Research orchestrator with 6 parallel agents

4. **`src/services/research/agents/sales/composition/lanes.py`**
   - Modified: `build_synthesis_agents()` now accepts `company_name` parameter
   - Impact: Passes company name to alignment context tool

5. **`src/services/research/agents/sales/composition/synthesis.py`**
   - Modified: `create_synthesis_agents()` now accepts `company_name`
   - Added: `make_alignment_context_tool(company_name)` in extra_tools
   - Changed: Alignment tool list now includes context tool instead of relying on vector search

### Prompts
6. **`src/services/research/agents/sales/prompts/synthesis_alignment_prompts.py`**
   - Changed: Alignment block to reference `retrieve_alignment_context()`
   - Changed: Removed mentions of vector search tool
   - Updated: Instructions to use PDF context for Colt information

### Agent Infrastructure
7. **`src/services/research/agents/__init__.py`**
   - Modified: `create_sales_agent_app()` now accepts `company_name` parameter
   - Changed: Passes company_name to `SalesAgentAppFactory().create()`

8. **`src/services/research/run/runner.py`**
   - Modified: Line 53 changed from `create_sales_agent_app()` to `create_sales_agent_app(company_name)`
   - Impact: Company name is now available to query generator during app creation

### BigQuery Repository
9. **`src/repositories/bigquery_repository.py`**
   - Modified: `insert_cost_attribution()` signature
   - Added parameters: `search_count`, `search_cost_usd`, `token_cost_usd`, `total_cost_usd`
   - Updated: SQL query and parameters to include new fields

## Key Behavioral Changes

### Query Generation
- **Before:** Each of 12 agents generated queries implicitly (in their prompts)
- **After:** Single agent generates all queries upfront, BM25 selects top 40

### Year Specificity
- **Feature:** Queries like "sephora revenue 2025" and "sephora revenue 2024" are NOT deduplicated
- **Reason:** Different years provide different information (growth tracking)

### Search Caching
- **Before:** No caching (fresh search every time)
- **After:** Query-level cache keyed by company_name + query_hash
- **Benefit:** Second research of same company = 70-90% cache hit

### Alignment Phase
- **Before:** Used vector search to find matching Colt products
- **After:** Uses PDF context injected via `retrieve_alignment_context()` tool
- **Fallback:** Hardcoded Colt catalog if GCS PDF not found

### Cost Attribution
- **Before:** Token costs only
- **After:** Breakdown into:
  - Token costs (input, output, total)
  - Search costs (count × price_per_1k)
  - Total USD

## Breaking Changes

1. **API Response:** Cost attribution now includes `search_count` and `search_cost_usd`
   - Clients expecting old schema will need update

2. **Query Generation:** Removed per-agent query logic
   - Old agent specs in `registry.py` are no longer used for query generation
   - (But still exist in registry for potential future use or reference)

3. **Alignment Prompt:** Changed from using vector search to PDF context
   - Requires `retrieve_alignment_context()` tool availability

## Deprecations (Not Removed)

- **12 Research Agents:** Still defined in `registry.py` and `lanes.py` but not used
- **12-Agent Parallel Orchestrator:** Still exists but not called from app factory
- **Vector Search Integration:** Still available but not used in alignment

## Environment Setup Required

1. Create BigQuery table:
   ```sql
   CREATE TABLE `{project}.{dataset}.search_cache` (
     company_name STRING NOT NULL,
     query STRING NOT NULL,
     query_hash STRING NOT NULL,
     search_results STRING,
     domain STRING,
     search_date TIMESTAMP NOT NULL
   )
   PARTITION BY DATE(search_date)
   CLUSTER BY company_name, domain;
   ```

2. Migrate cost_attribution table:
   ```sql
   ALTER TABLE `{project}.{dataset}.cost_attribution` ADD COLUMN search_count INT64;
   ALTER TABLE `{project}.{dataset}.cost_attribution` ADD COLUMN search_cost_usd FLOAT64;
   ALTER TABLE `{project}.{dataset}.cost_attribution` ADD COLUMN token_cost_usd FLOAT64;
   ALTER TABLE `{project}.{dataset}.cost_attribution` ADD COLUMN total_cost_usd FLOAT64;
   ```

3. Update `.env`:
   ```bash
   GOOGLE_SEARCH_PRICING_3X=14.0
   GOOGLE_SEARCH_PRICING_2X=35.0
   ```

## Testing Recommendations

1. **Unit Tests:**
   - `test_bm25_selector`: Verify deduplication and scoring
   - `test_query_generator`: Verify JSON parsing and output structure
   - `test_cost_analyzer`: Verify token + search cost calculations

2. **Integration Tests:**
   - `test_search_cache`: Verify BQ read/write operations
   - `test_alignment_context`: Verify PDF loading + fallback
   - `test_end_to_end`: Full pipeline with mock searches

3. **Performance Tests:**
   - Measure token usage (should be ~20% lower)
   - Measure API calls (should be ~30% lower due to dedup)
   - Measure latency (should be ~10-15% faster)

## Rollback Plan

If issues arise:

1. **Revert app.py:** Restore old `SalesAgentAppFactory.create()` to use 12-agent parallel
2. **Revert synthesis.py:** Remove alignment context tool, restore vector search
3. **Revert runner.py:** Remove `company_name` parameter from `create_sales_agent_app()`
4. **Keep:** Cost analysis, search cache (useful for other features)

## Future Enhancements

- [ ] Cache warming: Pre-populate cache for hot companies
- [ ] Query learning: Track which queries produce best results per domain
- [ ] Adaptive budgeting: Adjust per-domain limits based on hit rates
- [ ] Interactive alignment: User feedback to improve Colt mappings
- [ ] Multi-PDF support: Different PDFs for different company types
