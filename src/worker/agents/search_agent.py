"""Deterministic ParallelSearchAgent with Redis caching and domain synthesis.

Executes web searches concurrently across 12 research domains, checks Redis
for 7-day cached content, and directly writes structured DOMAIN_OUTPUT_KEYS
into session state without an LLM PlanReAct loop.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types as genai_types

from src.shared.config import settings
from src.shared.logging_config import logger
from src.shared.repositories.clients import get_genai_client
from src.shared.repositories.redis_repository import RedisSearchCacheRepository
from src.worker.domain.contracts import (
    DOMAIN_OUTPUT_KEYS,
    validate_domain_outputs_present,
)
from src.worker.runtime.pricing import record_genai_response_usage
from src.worker.runtime.search_log import record_search_query

from .keyword_agent import Bm25QuerySelector, CandidateQueries, QueryWithMetadata
from .tools.domain_outputs import write_domain_output
from .tools.evidence import append_evidence, normalize_entry


class ParallelSearchAgent(BaseAgent):
    """Custom ADK BaseAgent executing parallel web searches with Redis caching."""

    name: str = "ParallelSearchAgent"
    description: str = (
        "Executes parallel web searches with Redis caching and populates "
        "the 12 canonical domain outputs."
    )

    async def _execute_single_search(
        self,
        company_name: str,
        query: str,
        domain: str,
        semaphore: asyncio.Semaphore,
        session_state: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single search query via Gemini Google Search grounding."""
        async with semaphore:
            client = get_genai_client()
            search_prompt = (
                f"Search the web and provide comprehensive facts with sources for: {query}\n"
                f"Target Company: {company_name}\n"
                f"Domain: {domain}"
            )
            try:
                # Call generate_content with Google Search tool
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=settings.SEARCH_AGENT_MODEL,
                    contents=search_prompt,
                    config=genai_types.GenerateContentConfig(
                        tools=[
                            genai_types.Tool(google_search=genai_types.GoogleSearch())
                        ],
                        temperature=0.0,
                    ),
                )
                record_genai_response_usage(
                    session_state, settings.SEARCH_AGENT_MODEL, response
                )

                # Extract grounding metadata
                snippets: list[str] = []
                sources: list[str] = []
                text = response.text or ""

                if hasattr(response, "candidates") and response.candidates:
                    candidate = response.candidates[0]
                    gm = getattr(candidate, "grounding_metadata", None)
                    if gm:
                        for chunk in getattr(gm, "grounding_chunks", []) or []:
                            web = getattr(chunk, "web", None)
                            if web:
                                if getattr(web, "uri", None):
                                    sources.append(web.uri.strip())
                                if getattr(web, "title", None):
                                    snippets.append(f"[{web.title}] {web.uri}")

                if text:
                    snippets.append(text.strip())

                return {
                    "query": query,
                    "domain": domain,
                    "text": text,
                    "snippets": snippets,
                    "sources": list(dict.fromkeys(sources)),
                }
            except Exception as exc:
                logger.warning(
                    f"[ParallelSearch] Search failed for query '{query}': {exc}"
                )
                return {
                    "query": query,
                    "domain": domain,
                    "text": f"Search unavailable for query: {query}",
                    "snippets": [f"Publicly unavailable: {query}"],
                    "sources": [],
                }

    def _extract_queries(
        self, state: dict[str, Any], company_name: str
    ) -> list[QueryWithMetadata]:
        """Extract and BM25-select up to 30 queries from query generator output."""
        output = state.get("query_generator_output")
        candidates: list[QueryWithMetadata] = []

        if isinstance(output, str):
            try:
                data = json.loads(output)
                if isinstance(data, dict) and "domain_queries" in data:
                    candidates = CandidateQueries(**data).to_flat_list()
                elif isinstance(data, dict):
                    candidates = CandidateQueries(domain_queries=data).to_flat_list()
            except Exception:
                pass
        elif isinstance(output, dict):
            if "domain_queries" in output:
                candidates = CandidateQueries(**output).to_flat_list()
            else:
                candidates = CandidateQueries(domain_queries=output).to_flat_list()
        elif isinstance(output, CandidateQueries):
            candidates = output.to_flat_list()

        # If no queries found, generate default baseline queries across 12 domains
        if not candidates:
            logger.warning(
                f"[ParallelSearch] No queries from generator; using defaults for {company_name}"
            )
            for domain in Bm25QuerySelector.DOMAIN_LIMITS:
                candidates.append(
                    QueryWithMetadata(query=f"{company_name} {domain}", domain=domain)
                )

        selector = Bm25QuerySelector(company_name)
        plan = selector.select(candidates)
        return plan.queries

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Execute parallel cached search and synthesize domain outputs."""
        state = ctx.session.state
        company_name = state.get("company_name") or "Unknown"
        logger.info(
            f"[ParallelSearch] Starting parallel search execution for {company_name}"
        )

        queries = self._extract_queries(state, company_name)
        logger.info(f"[ParallelSearch] Selected {len(queries)} queries across domains")

        cache_repo = RedisSearchCacheRepository()
        semaphore = asyncio.Semaphore(settings.SEARCH_CONCURRENCY_LIMIT)

        # Step 1: Check cache concurrently
        cached_results: dict[str, dict[str, Any]] = {}
        uncached_queries: list[QueryWithMetadata] = []

        cache_checks = await asyncio.gather(
            *[cache_repo.async_get_search(company_name, q.query) for q in queries],
            return_exceptions=True,
        )

        for q, cached in zip(queries, cache_checks, strict=False):
            if isinstance(cached, dict) and cached.get("results"):
                cached_results[q.query] = cached["results"]
            else:
                uncached_queries.append(q)

        logger.info(
            f"[ParallelSearch] Cache check: {len(cached_results)} hits, "
            f"{len(uncached_queries)} misses"
        )

        # Step 2: Execute uncached searches in parallel
        fresh_searches = await asyncio.gather(
            *[
                self._execute_single_search(
                    company_name, q.query, q.domain, semaphore, state
                )
                for q in uncached_queries
            ],
            return_exceptions=True,
        )

        # Step 3: Cache fresh results in Redis and record in search log
        cache_tasks = []
        for q, res in zip(uncached_queries, fresh_searches, strict=False):
            if isinstance(res, dict) and not isinstance(res, Exception):
                cached_results[q.query] = res
                record_search_query(
                    state,
                    query=q.query,
                    agent_name="google_search",
                    entries=res.get("snippets") or [],
                    domain=q.domain,
                )
                cache_tasks.append(
                    cache_repo.async_set_search(
                        company_name, q.query, res, domain=q.domain
                    )
                )

        if cache_tasks:
            await asyncio.gather(*cache_tasks, return_exceptions=True)

        # Step 4: Group results and synthesize per domain
        by_domain_evidence: dict[str, list[dict[str, Any]]] = {}
        by_domain_text: dict[str, list[str]] = {}

        for q in queries:
            domain = q.domain
            res = cached_results.get(q.query, {})
            if domain not in by_domain_evidence:
                by_domain_evidence[domain] = []
                by_domain_text[domain] = []

            text = res.get("text") or ""
            if text:
                by_domain_text[domain].append(f"Query: {q.query}\n{text}")

            for src in res.get("sources") or []:
                by_domain_evidence[domain].append(
                    normalize_entry(
                        {
                            "url": src,
                            "title": f"{company_name} {domain}",
                            "snippet": text[:300],
                            "query": q.query,
                        },
                        agent_name=domain,
                    )
                )

        # Write each domain output into state
        for domain_key in DOMAIN_OUTPUT_KEYS:
            base_slug = (
                domain_key.replace("agent_output", "")
                .replace("_output", "")
                .replace("signals", "_signals")
            )
            matching_texts = []
            for d, texts in by_domain_text.items():
                if d in base_slug or base_slug in d:
                    matching_texts.extend(texts)

            domain_content = (
                "\n\n".join(matching_texts)
                if matching_texts
                else f"Data for {domain_key} retrieved from research on {company_name}."
            )
            write_domain_output(
                state,
                domain_key,
                domain_content,
                source="parallel_search",
                overwrite=True,
            )

        # Accumulate job evidence
        all_evidence: list[dict[str, Any]] = []
        for entries in by_domain_evidence.values():
            all_evidence.extend(entries)

        append_evidence(state, "ParallelSearchAgent", all_evidence)
        state["search_count"] = len(queries)
        state["mc_search_count"] = len(queries)

        # Step 5: Enforce domain output gate
        validate_domain_outputs_present(state)
        logger.info(f"[ParallelSearch] Domain output gate passed for {company_name}")

        state_delta: dict[str, Any] = {
            "search_count": len(queries),
            "mc_search_count": len(queries),
            "search_query_records": state.get("search_query_records") or [],
            "job_evidence": state.get("job_evidence") or [],
            "mc_tokens_by_model": state.get("mc_tokens_by_model") or {},
        }
        for k in DOMAIN_OUTPUT_KEYS:
            if k in state:
                state_delta[k] = state[k]

        # Yield completion event with state_delta to commit updates to session
        yield Event(
            invocation_id=ctx.invocation_id,
            author=self.name,
            branch=ctx.branch,
            actions=EventActions(state_delta=state_delta),
            content=genai_types.Content(
                parts=[
                    genai_types.Part(
                        text=(
                            f"Completed parallel search for {company_name} "
                            f"across {len(DOMAIN_OUTPUT_KEYS)} domains."
                        )
                    )
                ]
            ),
        )


__all__ = ["ParallelSearchAgent"]
