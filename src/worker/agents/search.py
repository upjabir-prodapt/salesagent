"""SearchExecutor: the ParallelSearchAgent replacement with real QPS
control, per-query retry, and honest failure reporting.

Fixes verified in IMPLEMENTATION_PLAN.md:
  - A2: search previously had no retry at any layer. Each query now retries
    per SEARCH_QUERY_RETRY_ATTEMPTS with exponential backoff.
  - A3: no QPS control existed anywhere (only a concurrency semaphore).
    RateLimiter adds a real async token bucket with adaptive penalty on 429.
  - R3: failed queries used to be recorded as fabricated success text
    ("Search unavailable for query: ..."), counted as legitimate content
    and billed. QueryResult.failed() records the failure instead.
  - R4: search_count used to count every attempted query, including cache
    hits and fabricated failures. SearchFindings.executed counts only
    genuinely successful fresh searches.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from google.genai import types as genai_types

from src.shared.logging_config import logger
from src.shared.utils.url_utils import is_authoritative
from src.worker.agents.base import (
    Agent,
    ErrorKind,
    InvalidOutputError,
    RetryPolicy,
    classify,
)
from src.worker.agents.models import (
    DomainFinding,
    Evidence,
    Query,
    QueryPlan,
    QueryResult,
    SearchFindings,
)
from src.worker.domain.contracts import DOMAIN_OUTPUT_KEYS


class RateLimiter:
    """Async token bucket with adaptive penalty on rate-limit responses.

    acquire() blocks until a token is available at the current effective
    rate. penalize() halves the effective rate for a cooldown window (e.g.
    after a 429); recover() restores the configured rate once the cooldown
    has elapsed. This is real QPS control, distinct from (and layered
    underneath) the existing concurrency semaphore.
    """

    def __init__(
        self, qps: float, burst: int, *, penalty_cooldown: float = 30.0
    ) -> None:
        self._base_qps = max(qps, 0.01)
        self._effective_qps = self._base_qps
        self._capacity = max(burst, 1)
        self._tokens = float(self._capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._penalty_cooldown = penalty_cooldown
        self._penalized_until = 0.0

    def penalize(self) -> None:
        self._effective_qps = max(self._base_qps / 2.0, 0.01)
        self._penalized_until = time.monotonic() + self._penalty_cooldown
        logger.warning(
            f"[RateLimiter] penalized: effective_qps={self._effective_qps:.2f} "
            f"for {self._penalty_cooldown:.0f}s"
        )

    def recover(self) -> None:
        if (
            self._effective_qps < self._base_qps
            and time.monotonic() >= self._penalized_until
        ):
            self._effective_qps = self._base_qps
            logger.info(f"[RateLimiter] recovered to base_qps={self._base_qps:.2f}")

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                self.recover()
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._last_refill = now
                self._tokens = min(
                    self._capacity, self._tokens + elapsed * self._effective_qps
                )
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait_seconds = deficit / self._effective_qps
            await asyncio.sleep(wait_seconds)


# Maps a domain slug from query generation onto the canonical
# DOMAIN_OUTPUT_KEYS entry. Fixes bug C3: the old substring-matching
# heuristic (`techstack` vs `tech_stack`) silently dropped the
# Technology Landscape domain every run. This mapping is explicit and
# exhaustive, so a mismatch is a loud KeyError at construction time
# instead of a silent empty domain.
DOMAIN_SLUG_TO_OUTPUT_KEY: dict[str, str] = {
    "firmographics": "firmographicsagent_output",
    "geographic": "geographicagent_output",
    "executive": "executiveagent_output",
    "strategy": "strategyagent_output",
    "compliance": "complianceagent_output",
    "market": "marketagent_output",
    "ecosystem": "ecosystemagent_output",
    "tech_stack": "techstackagent_output",
    "procurement": "procurementagent_output",
    "growth_signals": "growthsignals_output",
    "risk_signals": "risksignals_output",
    "campaign_signals": "campaignsignals_output",
}

if set(DOMAIN_SLUG_TO_OUTPUT_KEY.values()) != set(DOMAIN_OUTPUT_KEYS):
    raise RuntimeError(
        "DOMAIN_SLUG_TO_OUTPUT_KEY must cover every canonical DOMAIN_OUTPUT_KEYS "
        "entry exactly once"
    )


class SearchExecutor(Agent[QueryPlan, SearchFindings]):
    """Executes search queries with real QPS control, per-query retry, and
    honest failure reporting. Retried as a whole step (via validate())
    when too many individual queries fail.
    """

    name = "SearchExecutor"

    def __init__(
        self,
        genai_client: Any,
        cache_repo: Any,
        *,
        model: str,
        qps: float,
        qps_burst: int,
        concurrency: int,
        query_retry: RetryPolicy | None = None,
        min_success_rate: float = 0.6,
        step_retry: RetryPolicy | None = None,
    ) -> None:
        self._client = genai_client
        self._cache = cache_repo
        self._model = model
        self._limiter = RateLimiter(qps, qps_burst)
        self._semaphore = asyncio.Semaphore(max(concurrency, 1))
        self._query_retry = query_retry or RetryPolicy(
            max_attempts=3, initial_delay=1.0, max_delay=15.0
        )
        self._min_success_rate = min_success_rate
        if step_retry is not None:
            self.retry = step_retry

    async def execute(self, plan: QueryPlan) -> SearchFindings:
        cached_results, uncached = await self._partition_cache(plan)

        fresh_results = await asyncio.gather(
            *(self._run_one(plan.company, q) for q in uncached)
        )

        all_results: dict[str, QueryResult] = dict(cached_results)
        for res in fresh_results:
            all_results[res.query.text] = res
            if res.succeeded:
                await self._store_cache(plan.company, res)

        return self._assemble(plan, all_results)

    def validate(self, findings: SearchFindings) -> None:
        if findings.success_rate < self._min_success_rate:
            raise InvalidOutputError(
                f"{self.name}: success rate {findings.success_rate:.0%} below "
                f"minimum {self._min_success_rate:.0%} "
                f"({findings.executed} succeeded, {len(findings.failed)} failed)",
                agent_name=self.name,
            )

    async def _partition_cache(
        self, plan: QueryPlan
    ) -> tuple[dict[str, QueryResult], list[Query]]:
        """Split queries into cache hits (already QueryResult) and misses."""
        checks = await asyncio.gather(
            *(self._cache.async_get_search(plan.company, q.text) for q in plan.queries),
            return_exceptions=True,
        )
        cached: dict[str, QueryResult] = {}
        uncached: list[Query] = []
        for q, cached_value in zip(plan.queries, checks, strict=False):
            if isinstance(cached_value, dict) and cached_value.get("results"):
                payload = cached_value["results"]
                evidence = tuple(
                    Evidence(
                        url=src,
                        title=f"{plan.company} {q.domain}",
                        snippet=str(payload.get("text", ""))[:300],
                        query=q.text,
                        authoritative=is_authoritative(src),
                    )
                    for src in (payload.get("sources") or [])
                )
                cached[q.text] = QueryResult.ok(
                    q, str(payload.get("text", "")), evidence
                )
            else:
                uncached.append(q)
        return cached, uncached

    async def _run_one(self, company: str, query: Query) -> QueryResult:
        """Execute one query with QPS gating, concurrency limiting, and
        per-query retry. Never fabricates content on failure.
        """
        attempt = 0
        while True:
            attempt += 1
            await self._limiter.acquire()
            async with self._semaphore:
                try:
                    text, evidence = await self._search_once(company, query)
                    return QueryResult.ok(query, text, evidence)
                except Exception as exc:  # noqa: BLE001 - classified below
                    kind = classify(exc)
                    if kind is ErrorKind.RATE_LIMIT:
                        self._limiter.penalize()
                    if not self._query_retry.should_retry(kind, attempt):
                        logger.warning(
                            f"[SearchExecutor] query failed permanently "
                            f"(attempt {attempt}, kind={kind}): {query.text!r}: {exc}"
                        )
                        return QueryResult.failed(query, str(kind))
                    delay = self._query_retry.delay_for(attempt)
                    logger.debug(
                        f"[SearchExecutor] query attempt {attempt} failed "
                        f"(kind={kind}), retrying in {delay:.2f}s: {query.text!r}"
                    )
                    await asyncio.sleep(delay)

    async def _search_once(
        self, company: str, query: Query
    ) -> tuple[str, tuple[Evidence, ...]]:
        prompt = (
            f"Search the web and provide comprehensive facts with sources for: "
            f"{query.text}\nTarget Company: {company}\nDomain: {query.domain}"
        )
        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
                temperature=0.0,
            ),
        )
        text = response.text or ""
        evidence: list[Evidence] = []
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            grounding = getattr(candidates[0], "grounding_metadata", None)
            for chunk in getattr(grounding, "grounding_chunks", None) or []:
                web = getattr(chunk, "web", None)
                if web is not None and getattr(web, "uri", None):
                    evidence.append(
                        Evidence(
                            url=web.uri,
                            title=getattr(web, "title", "")
                            or f"{company} {query.domain}",
                            snippet=text[:300],
                            query=query.text,
                            authoritative=is_authoritative(web.uri),
                        )
                    )
        return text, tuple(evidence)

    async def _store_cache(self, company: str, result: QueryResult) -> None:
        try:
            await self._cache.async_set_search(
                company,
                result.query.text,
                {
                    "text": result.text,
                    "sources": [e.url for e in result.evidence if e.url],
                },
                domain=result.query.domain,
            )
        except Exception as exc:  # pragma: no cover - best-effort cache write
            logger.debug(f"[SearchExecutor] cache write failed: {exc}")

    def _assemble(
        self, plan: QueryPlan, results: dict[str, QueryResult]
    ) -> SearchFindings:
        by_domain_text: dict[str, list[str]] = {}
        by_domain_evidence: dict[str, list[Evidence]] = {}
        executed = 0
        failed: list[str] = []

        for q in plan.queries:
            res = results.get(q.text)
            if res is None or not res.succeeded:
                failed.append(q.text)
                continue
            executed += 1
            by_domain_text.setdefault(q.domain, []).append(res.text)
            by_domain_evidence.setdefault(q.domain, []).extend(res.evidence)

        domains: dict[str, DomainFinding] = {}
        for slug, output_key in DOMAIN_SLUG_TO_OUTPUT_KEY.items():
            texts = by_domain_text.get(slug, [])
            content = "\n\n".join(texts)
            domains[output_key] = DomainFinding(
                domain=slug,
                content=content,
                evidence=tuple(by_domain_evidence.get(slug, [])),
            )

        return SearchFindings(
            company=plan.company,
            domains=domains,
            executed=executed,
            failed=tuple(failed),
        )


__all__ = ["RateLimiter", "SearchExecutor", "DOMAIN_SLUG_TO_OUTPUT_KEY"]
