# Functional Design & Code Plan — Unit 5: Colt Catalog Context Caching

## 1. Overview
Wraps the Colt product catalog text in Gemini explicit context caching to eliminate re-sending large catalog prompt tokens on every alignment call.

## 2. Implementation
- `get_or_create_colt_context_cache()` in `gcs_pdf_loader.py`
- Attempts `client.caches.create` with TTL of 3600 seconds.
- Falls back to returning catalog text directly when running locally or on test environments without Vertex context cache support.
- `make_alignment_context_tool(company_name)` provides `retrieve_alignment_context()` tool.
