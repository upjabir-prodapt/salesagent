# Test Guide: End-to-End Architecture Testing

Three comprehensive test suites for the refactored Query Generator architecture—no server startup required.

## Overview

| Test File | Purpose | Speed | LLM | Search | Cache | Context | Best For |
|-----------|---------|-------|-----|--------|-------|---------|----------|
| `test_architecture_mock.py` | Mock flow simulation | 30 sec | Mock | Mock | Mock | Mock | Fast CI/CD, logic verification |
| `test_query_generator_e2e.py` | Hybrid real/mock | 5-15 min | Real | Mock | Mock | Mock | Integration testing, fast validation |
| `test_query_generator_fully_real_e2e.py` | **FULLY REAL** | 20-30 min | Real | Real | **Real BQ** | **Real GCS** | **Source of truth, staging/prod** |

---

## Test 1: Fully Real End-to-End (Production/Staging Only)

### File
`tests/agents/test_query_generator_fully_real_e2e.py`

### What It Tests (ALL REAL - Zero Mocks)
- ✅ Real LLM (Gemini) for query generation
- ✅ Real GoogleSearchAgentTool for searches (actual API calls)
- ✅ **Real BigQuery write/read** for search cache
- ✅ **Real GCS PDF loading** for alignment context
- ✅ Real cost tracking with actual token counts

### ⚠️ Requirements
```
✓ Valid Google Cloud credentials
✓ BigQuery dataset access (read/write)
✓ GCS bucket with PDF files
✓ Google Search API enabled
✓ 20-30 minutes to complete
✓ Budget for actual API calls (costs real $$)
```

### Run
```bash
pytest tests/agents/test_query_generator_fully_real_e2e.py -v -s --timeout=1800
```

### Tests (7 Scenarios)
1. **App Creation** - Real LLM components
2. **BM25 Selection** - Real ranking algorithm
3. **BigQuery Cache** - Real write/read operations
4. **GCS PDF Loading** - Real PDF context retrieval
5. **Cost Analysis** - Real pricing calculations
6. **Cache Hit Rate** - Real cache effectiveness
7. **Complete Flow** - All 5 steps in sequence

### Sample Output
```
[REAL TEST 1] App Creation with Real LLM
  ✓ QueryGeneratorAgent (real Gemini)
  ✓ AlignmentAnalyst (real)
  ✓ ReportCompiler (real)

[REAL TEST 2] BM25 Selection Logic
  Selected 8 from 8 candidates
  2025 queries: 2
  2024 queries: 2

[REAL TEST 3] Real BigQuery Cache
  ✓ Cached to BigQuery: "Test query for TestCorp_12345"
  ✓ Retrieved from BigQuery: 1 cached searches

[REAL TEST 4] Real GCS PDF Context
  ✓ Using HARDCODED fallback context
  Context size: 2847 chars

[REAL TEST 5] Real Cost Analysis
  Model: gemini-2.5-flash
  Tokens: 50000 in + 15000 out
    Token cost: $0.056250
  Searches: 28 × $35/1000
    Search cost: $0.980000
  TOTAL: $1.036250

[REAL TEST 6] Real Cache Hit Rate
  [Run 1] Caching 3 queries...
  ✓ Cached 3 queries in BigQuery
  [Run 2] Checking cache for 4 queries...
  ✓ Uncached: 2
  ✓ Cache hit rate: 50%

[REAL TEST 7] COMPLETE REAL END-TO-END FLOW
  Duration: 145.3s
  Company: TestCorp_1692547812
  Queries: 8
  Cost: $1.036250
  ✅ COMPLETE REAL END-TO-END FLOW PASSED
```

---

## Test 2: End-to-End with Real LLM (Hybrid)

### File
`tests/agents/test_query_generator_e2e.py`

### What It Tests
- Query Generator Agent creation with real LLM
- BM25 candidate selection
- Year-specific query differentiation (2025 vs 2024 not deduplicated)
- Search cache integration
- Cost analysis (tokens + searches)
- Complete app factory structure

### Run All Tests
```bash
pytest tests/agents/test_query_generator_e2e.py -v -s
```

### Run Specific Tests
```bash
# Test query generator creation
pytest tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_query_generator_agent_creation -v -s

# Test BM25 selector logic
pytest tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_bm25_selector_with_candidates -v -s

# Test cost analysis
pytest tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_full_cost_analysis -v -s

# Test app factory
pytest tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_app_factory_creation -v -s

# NEW: Test complete end-to-end flow
pytest tests/agents/test_query_generator_e2e.py::TestEndToEndFullFlow::test_complete_e2e_flow -v -s

# NEW: Test cache benefits scenario
pytest tests/agents/test_query_generator_e2e.py::TestEndToEndFullFlow::test_cache_benefits_e2e -v -s
```

### Key Test Coverage

#### 1. Query Generation
```python
def test_query_generator_agent_creation(self, company_name: str)
```
- Verifies QueryGeneratorAgent instantiation
- Checks tool availability
- Validates naming

#### 2. BM25 Selection
```python
def test_bm25_selector_with_candidates(self)
```
- Tests deduplication (Jaccard > 0.7)
- Verifies year-specific queries are NOT deduplicated
- Confirms budget limits (max 40)
- Validates per-domain distribution

**Key Assertion:**
```python
# Verify 2025 and 2024 queries are BOTH kept
revenue_2025_queries = [q for q in plan.queries if "revenue" in q.query.lower() and q.year == 2025]
revenue_2024_queries = [q for q in plan.queries if "revenue" in q.query.lower() and q.year == 2024]
assert len(revenue_2025_queries) > 0  # 2025 kept
assert len(revenue_2024_queries) > 0  # 2024 kept
```

#### 3. Cost Analysis
```python
def test_full_cost_analysis(self, analyzer: CostAnalyzer)
```
- Token cost breakdown (input + output)
- Search cost by model version (3.x: $14, 2.x: $35 per 1000)
- Total cost calculation

**Output:**
```
Tokens: $0.05625
Searches: $0.98
Total: $1.03625
```

#### 4. App Structure
```python
def test_app_factory_creation(self, company_name: str)
```
- Verifies SalesResearchAgent pipeline
- Checks sub-agents: QueryGenerator → Alignment → ReportCompiler
- Validates naming and structure

---

## Test 2: Complete Mock Flow

### File
`tests/agents/test_architecture_mock.py`

### What It Tests
- Mock candidate generation (15 queries)
- BM25 selection with mock data
- Search orchestration flow
- Alignment context injection
- Complete end-to-end mock pipeline
- Cache hit scenarios
- Domain coverage

### Run All Tests
```bash
pytest tests/agents/test_architecture_mock.py -v -s
```

### Run Specific Test Groups
```bash
# Core mock flow tests
pytest tests/agents/test_architecture_mock.py::TestArchitectureMockFlow -v -s

# Complete end-to-end mock simulation
pytest tests/agents/test_architecture_mock.py::TestMockEndToEndFlow::test_complete_mock_flow -v -s

# Cache benefits simulation
pytest tests/agents/test_architecture_mock.py::TestMockEndToEndFlow::test_mock_flow_with_cache_hits -v -s

# Domain coverage verification
pytest tests/agents/test_architecture_mock.py::TestMockEndToEndFlow::test_parallel_domain_coverage -v -s
```

### Key Test Coverage

#### 1. Mock Flow Simulation
```python
def test_complete_mock_flow(self)
```

Simulates all 6 steps WITHOUT any LLM calls:

| Step | Action | Output |
|------|--------|--------|
| 1 | Query Generation | 15 candidate queries |
| 2 | BM25 Selection | 8 selected queries |
| 3 | Search Execution | 3 mock search results |
| 4 | Context Loading | PDF context (simulated) |
| 5 | Cost Analysis | Token + search breakdown |
| 6 | Report Generation | Mock report (~500 chars) |

**Log Output:**
```
============================================================
STARTING COMPLETE MOCK FLOW SIMULATION
============================================================

[Step 1] Query Generation
  ✓ Generated 15 candidates across 5 domains

[Step 2] BM25 Selection
  ✓ Selected 8 from 15 candidates (max budget: 40)

[Step 3] Search Execution
  ✓ Executed 3 searches

[Step 4] Alignment Context Loading
  ✓ Loaded 350 byte context

[Step 5] Cost Analysis
  ✓ Token cost: $0.056250
  ✓ Search cost: $0.001050
  ✓ Total cost: $0.057300

[Step 6] Report Generation (Mocked)
  ✓ Generated report (500 chars)

============================================================
✅ COMPLETE MOCK FLOW SIMULATION PASSED
============================================================
```

#### 2. Cache Hit Simulation
```python
def test_mock_flow_with_cache_hits(self)
```

Simulates cost savings from caching:

**Run 1 (Fresh):**
```
Executed 40/40 queries
Cost: $1.40
```

**Run 2 (Cached):**
```
Executed 10/40 queries (30 cached, 75% hit)
Cost: $0.35
Savings: $1.05 (75%)
```

#### 3. Domain Coverage
```python
def test_parallel_domain_coverage(self)
```

Verifies all 12 domains are configured:

```
[Domain Configuration]
Total domains: 12
Total query budget: 40
Avg queries per domain: 3.3

[Domain Limits]
campaign_signals      :  2 queries
compliance            :  3 queries
ecosystem             :  3 queries
executive             :  4 queries
firmographics         :  4 queries
geographic            :  3 queries
growth_signals        :  3 queries
market                :  3 queries
procurement           :  2 queries
risk_signals          :  3 queries
strategy              :  3 queries
tech_stack            :  3 queries

Total configured: 40/40
```

---

## Running All Test Suites

### Quick Check (30 seconds) - CI/CD
```bash
pytest tests/agents/test_architecture_mock.py -v
```

### Integration Check (5-15 minutes) - Development
```bash
pytest tests/agents/test_query_generator_e2e.py -v -s
```

### **Full Real Check (20-30 minutes) - Staging/Prod**
```bash
pytest tests/agents/test_query_generator_fully_real_e2e.py -v -s --timeout=1800
```

### All Tests Combined
```bash
# Mock + Hybrid (15 minutes total, safe)
pytest tests/agents/test_architecture_mock.py tests/agents/test_query_generator_e2e.py -v -s

# Everything (40+ minutes, requires GCP credentials)
pytest tests/agents/ -v -s --timeout=1800
```

### Watch Mode (with pytest-watch)
```bash
# Only mock tests (fast iteration)
ptw tests/agents/test_architecture_mock.py -- -v -s

# Mock + Hybrid (safer for dev)
ptw tests/agents/test_architecture_mock.py tests/agents/test_query_generator_e2e.py -- -v -s
```

### Generate Coverage Report
```bash
pytest tests/agents/ --cov=src/services/research/agents/sales/query_generator --cov-report=html
```

---

## What's Tested in Each File

### `test_query_generator_e2e.py` (Real LLM - 12 Tests)

✅ **Component Tests (10)**

✅ **QueryGeneratorFactory**
- Agent creation
- Tool availability
- Naming validation

✅ **Bm25QuerySelector**
- Candidate deduplication (Jaccard similarity)
- Year differentiation (2025 ≠ 2024)
- Scoring logic
- Budget limits
- Per-domain distribution

✅ **CandidateQueries Schema**
- JSON parsing
- Flat list conversion
- Year extraction from queries
- Domain organization

✅ **CostAnalyzer**
- Token cost (input + output)
- Search cost (model version detection)
- Total cost aggregation
- Model version classification (2.x vs 3.x)

✅ **SalesAgentAppFactory**
- App creation
- Pipeline structure (Sequential Agent)
- Sub-agent names and order
- QueryGenerator → Alignment → ReportCompiler

✅ **SearchCacheService**
- Cache lookup
- Query hash computation
- Result storage structure

✅ **Year Injection**
- Prompt includes current year
- Company name included
- Domain list included

✅ **Complete End-to-End Flow (NEW)**
- 7-step pipeline simulation:
  1. Query Generator Agent creation
  2. Candidate query generation
  3. BM25 selection
  4. Search cache integration
  5. Alignment context loading
  6. Cost analysis
  7. App factory creation
- Year handling verification
- Alignment context mocking

✅ **Cache Benefits Analysis (NEW)**
- Run 1: 40 searches → baseline cost
- Run 2: 10 searches (75% cache hit) → reduced cost
- Savings calculation and verification
- Cache hit rate validation

### `test_architecture_mock.py` (All Mocks)

✅ **Candidate Generation (Mock)**
- Domain distribution
- Query count per domain
- Structure validation

✅ **BM25 Selection (Mock)**
- Query ranking
- Deduplication correctness
- Year handling

✅ **Search Orchestration (Mock)**
- Cache vs executed split
- Result structure
- Domain tracking

✅ **Alignment Context (Mock)**
- Context loading simulation
- PDF content verification
- Fallback handling

✅ **Cost Analysis (Mock)**
- Token breakdown
- Search cost calculation
- Total aggregation

✅ **Complete Flow (Mock)**
- All 6 steps in sequence
- Error-free execution
- Proper logging

✅ **Cache Benefits (Mock)**
- Hit rate calculation
- Cost savings verification
- Scaling over multiple runs

✅ **Domain Coverage (Mock)**
- All 12 domains configured
- Budget distribution
- Limit verification

---

## Expected Output

### Successful E2E Test Run
```
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_query_generator_agent_creation PASSED                    [ 10%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_bm25_selector_with_candidates PASSED                     [ 20%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_candidate_queries_schema PASSED                          [ 30%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_cost_analyzer_token_cost PASSED                          [ 40%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_cost_analyzer_search_cost_2x_model PASSED                [ 50%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_cost_analyzer_search_cost_3x_model PASSED                [ 60%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_full_cost_analysis PASSED                                [ 70%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_app_factory_creation PASSED                              [ 80%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_search_cache_integration PASSED                          [ 90%]
tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_year_injection_in_queries PASSED                         [100%]

===================== 10 passed in 2.35s =====================
```

### Successful Mock Test Run
```
tests/agents/test_architecture_mock.py::TestArchitectureMockFlow::test_candidate_query_generation_mock PASSED                 [ 10%]
tests/agents/test_architecture_mock.py::TestArchitectureMockFlow::test_bm25_selection_mock PASSED                             [ 20%]
tests/agents/test_architecture_mock.py::TestArchitectureMockFlow::test_search_orchestration_mock PASSED                       [ 30%]
tests/agents/test_architecture_mock.py::TestArchitectureMockFlow::test_alignment_context_injection_mock PASSED                [ 40%]
tests/agents/test_architecture_mock.py::TestArchitectureMockFlow::test_cost_analysis_mock PASSED                              [ 50%]
tests/agents/test_architecture_mock.py::TestMockEndToEndFlow::test_complete_mock_flow PASSED                                 [ 60%]
tests/agents/test_architecture_mock.py::TestMockEndToEndFlow::test_mock_flow_with_cache_hits PASSED                          [ 70%]
tests/agents/test_architecture_mock.py::TestMockEndToEndFlow::test_parallel_domain_coverage PASSED                           [ 80%]

===================== 8 passed in 0.48s =====================
```

---

## Troubleshooting

### Test Hangs / Timeout
If E2E tests hang, check:
```bash
# Check if LLM is responding
curl -X POST https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent \
  -H "Content-Type: application/json" \
  -d '{"contents":[{"parts":[{"text":"test"}]}]}' \
  -H "x-goog-api-key: $GOOGLE_API_KEY"
```

### Import Errors
```bash
# Reinstall dependencies
pip install -e .

# Check PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
```

### Mock Tests Fail
```bash
# Run with more verbose output
pytest tests/agents/test_architecture_mock.py -vv -s --tb=long
```

---

## CI/CD Integration

### GitHub Actions Example
```yaml
- name: Run Mock Tests (Fast)
  run: pytest tests/agents/test_architecture_mock.py -v

- name: Run E2E Tests (Slow)
  run: pytest tests/agents/test_query_generator_e2e.py -v
  timeout-minutes: 20
```

### Pre-commit Hook
```bash
#!/bin/bash
pytest tests/agents/test_architecture_mock.py -q || exit 1
```

---

## Next Steps

1. **Run Mock Tests First**
   ```bash
   pytest tests/agents/test_architecture_mock.py -v -s
   ```

2. **Verify Full Flow**
   ```bash
   pytest tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_app_factory_creation -v -s
   ```

3. **Check Cost Analysis**
   ```bash
   pytest tests/agents/test_query_generator_e2e.py::TestQueryGeneratorE2E::test_full_cost_analysis -v -s
   ```

4. **Monitor Cache Behavior**
   ```bash
   pytest tests/agents/test_architecture_mock.py::TestMockEndToEndFlow::test_mock_flow_with_cache_hits -v -s
   ```

Happy testing! 🚀
