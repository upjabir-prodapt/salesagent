"""Shared plumbing for the local, server-free end-to-end scripts.

Used by:
  * local_research_e2e.py -- the 4 agent steps only, plus fault injection
    for the search-timeout regression.
  * local_full_e2e.py     -- the whole worker task: pipeline, artifacts,
    report, PDF, LLM evaluation, cost attribution, telemetry flush.

Everything here is a test double or an environment fixup. None of it
changes production behavior: the scripts hand these objects to the real
constructors (build_research_pipeline, ResearchArtifactService,
ResearchFinalizationService, ResearchJobRunner, ResearchTaskHandler),
which already take their collaborators by injection.

What gets replaced, and why:
  * RedisSearchCacheRepository -> InMemorySearchCache. Memorystore is on
    a private VPC IP, unreachable off-cluster.
  * GCSRepository             -> LocalFileStore. Writes the same blob
    layout onto the local filesystem.
  * BigQueryRepository        -> RecordingBigQuery. Records the calls it
    would have made; no dataset is touched.
  * FirestoreSearchCacheRepo  -> RecordingFirestore. Same.
  * ProgressObserver/TracingObserver -> ConsoleObserver.

What is NOT replaced: every agent, every prompt, the retry policies, the
timeouts, the model calls, the guardrails, the BM25 verifier and the LLM
judge. Those are the point.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Environment bootstrap
#
# src.shared.config builds a module-level `settings` singleton at import
# time, so every env var has to be in place before the first `src.*`
# import. The scripts deliberately do NOT read .env / .env.worker.local:
# those point at the sandbox project, a private-IP Redis and a live Cloud
# Trace OTLP endpoint -- exactly what these runs exist to avoid.
# ---------------------------------------------------------------------------

# Settings fields the model requires but that belong to subsystems no
# local run constructs for real (no live BigQuery dataset, no GCS bucket,
# no FastAPI app).
_UNUSED_PLACEHOLDERS: dict[str, str] = {
    "APP_NAME": "sales-agent-local-e2e",
    "APP_VERSION": "0.0.0-local",
    "API_PREFIX": "/api/v1",
    "HOST": "127.0.0.1",
    "PORT": "0",
    "WORKERS": "1",
    "AGENT_EVENT_LOG_VERBOSE": "false",
    "BIGQUERY_DATASET": "unused_local",
    "BIGQUERY_TABLE": "unused_local",
    "BIGQUERY_COST_ATTRIBUTION_TABLE": "unused_local",
    "BIGQUERY_AGENT_TELEMETRY_TABLE": "unused_local",
    "BIGQUERY_USER_FEEDBACK_TABLE": "unused_local",
    "GCS_BUCKET_NAME": "unused-local",
    "SECRET_KEY": "local-e2e-not-a-real-secret",
    "ALGORITHM": "HS256",
    "ACCESS_TOKEN_EXPIRE_MINUTES": "30",
}


def ensure_ca_bundle(mode: str) -> str:
    """Make TLS verification work behind a corporate inspecting proxy.

    Such a proxy re-signs every connection with a private root that lives
    in the OS certificate store, which Python's certifi bundle knows
    nothing about -- so google-genai's httpx client fails with
    CERTIFICATE_VERIFY_FAILED ("self-signed certificate in certificate
    chain") before a single request reaches Vertex AI.

    "auto" concatenates certifi with the machine's trusted roots into one
    bundle and points SSL_CERT_FILE / REQUESTS_CA_BUNDLE /
    GRPC_DEFAULT_SSL_ROOTS_FILE_PATH at it (httpx, google-auth's requests
    transport, and grpc respectively). Verification stays fully on -- the
    proxy's root is trusted because the OS already trusts it. "off"
    changes nothing; a path is used verbatim.
    """
    if mode == "off":
        return "(unchanged)"

    if mode != "auto":
        bundle = Path(mode)
        if not bundle.is_file():
            raise SystemExit(f"--ca-bundle file not found: {bundle}")
    elif os.environ.get("SSL_CERT_FILE"):
        return f"{os.environ['SSL_CERT_FILE']} (from environment)"
    else:
        import ssl

        import certifi

        pems: list[str] = []
        for store in ("ROOT", "CA"):
            try:
                for der, enc, trust in ssl.enum_certificates(store):
                    if enc != "x509_asn":
                        continue
                    # trust is True (all purposes) or a set of EKU OIDs;
                    # 1.3.6.1.5.5.7.3.1 is serverAuth.
                    if trust is True or (
                        isinstance(trust, set)
                        and (True in trust or "1.3.6.1.5.5.7.3.1" in trust)
                    ):
                        pems.append(ssl.DER_cert_to_PEM_cert(der))
            except (AttributeError, OSError):
                # enum_certificates is Windows-only; elsewhere certifi
                # plus the system default is already correct.
                pass
        if not pems:
            return "(certifi only; no OS trust store to merge)"
        bundle = REPO_ROOT / "out" / "local-e2e" / "_ca-bundle.pem"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        blocks = [Path(certifi.where()).read_text(encoding="utf-8"), *pems]
        bundle.write_text("\n".join(blocks), encoding="utf-8")

    os.environ["SSL_CERT_FILE"] = str(bundle)
    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = str(bundle)
    return str(bundle)


def add_common_args(p: argparse.ArgumentParser) -> None:
    """Credentials, region, model and search-tuning flags shared by both scripts."""
    p.add_argument("-c", "--company", default="Societe Generale")
    p.add_argument(
        "--credentials",
        default="service_account.json",
        help="Service-account JSON key (default: ./service_account.json)",
    )
    p.add_argument("--project", default="", help="Overrides the key's project_id")
    p.add_argument("--quota-project", default="")
    p.add_argument("--location", default="europe-west1")
    p.add_argument(
        "--vertex-location",
        default="",
        help="Gemini inference region (default: --location). The repo's "
        "service-account key needs 'global' -- gemini-3.5-flash is not "
        "served from that project's regional endpoints.",
    )
    p.add_argument("--model", default="gemini-3.5-flash")
    p.add_argument("--search-model", default="")

    p.add_argument("--qps", type=float, default=4.0)
    p.add_argument("--qps-burst", type=int, default=8)
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument(
        "--query-timeout",
        type=float,
        default=60.0,
        help="Per-query deadline (SEARCH_TIMEOUT_SECONDS).",
    )
    p.add_argument("--query-retry-attempts", type=int, default=3)
    p.add_argument(
        "--step-timeout",
        type=float,
        default=300.0,
        help="SearchExecutor step deadline (SEARCH_STEP_TIMEOUT_SECONDS).",
    )
    p.add_argument("--min-success-rate", type=float, default=0.6)
    p.add_argument(
        "--http-timeout",
        type=float,
        default=120.0,
        help="GENAI_HTTP_TIMEOUT_SECONDS on the shared genai client.",
    )

    p.add_argument(
        "--cache-file",
        default="",
        help="Persist the in-memory search cache here so reruns are free. "
        "Default: <out>/search-cache.json",
    )
    p.add_argument("--no-cache", action="store_true", help="Disable cache persistence.")
    p.add_argument(
        "--ca-bundle",
        default="auto",
        help="TLS trust roots: 'auto' merges certifi with the OS trust "
        "store (needed behind a corporate inspecting proxy), 'off' leaves "
        "the environment alone, or pass a PEM path. Default: auto.",
    )
    p.add_argument(
        "--out", default="", help="Output dir (default under out/local-e2e)."
    )
    p.add_argument("--log-level", default="INFO")


def bootstrap_env(args: argparse.Namespace) -> dict[str, str]:
    """Populate os.environ for a credentialed, telemetry-free local run.

    Returns the settings that actually influence the run, for the summary.
    """
    if "src.shared.config" in sys.modules:  # pragma: no cover - guard
        raise RuntimeError(
            "src.shared.config was imported before bootstrap_env(); the "
            "settings singleton is already frozen with the wrong values."
        )

    creds = Path(args.credentials)
    if not creds.is_absolute():
        creds = (REPO_ROOT / creds).resolve()
    if not creds.is_file():
        raise SystemExit(f"Service-account key not found: {creds}")

    sa = json.loads(creds.read_text(encoding="utf-8"))
    project = args.project or sa.get("project_id")
    if not project:
        raise SystemExit(
            f"--project not given and {creds.name} has no project_id field."
        )

    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds)
    ca_bundle = ensure_ca_bundle(args.ca_bundle)

    # Do not let a repo .env / /secrets/.env leak sandbox config in.
    os.environ["DOTENV_DISABLE"] = "1"

    # Kill every telemetry path. No exporter is ever configured (nothing
    # calls otel_setup), so the OTel API hands out no-op tracers and the
    # TracingObserver / @traced decorators become free no-ops. These vars
    # make that explicit rather than incidental.
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["OTEL_ENABLED"] = "false"
    os.environ["OTEL_TRACES_EXPORTER"] = "none"
    os.environ["OTEL_METRICS_EXPORTER"] = "none"
    os.environ["OTEL_LOGS_EXPORTER"] = "none"
    os.environ["OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED"] = "false"
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""
    os.environ["OTEL_RESOURCE_ATTRIBUTES"] = ""

    os.environ.update(_UNUSED_PLACEHOLDERS)

    # IS_LOCAL=True is load-bearing beyond logging: it lets
    # gcs_pdf_loader.load_mounted_colt_catalog_pdf() fall back to the
    # embedded Colt catalog instead of raising when the mounted PDF is
    # absent, which is what makes AlignmentAnalyst runnable off-cluster.
    live: dict[str, str] = {
        "IS_LOCAL": "True",
        "APP_ROLE": "worker",
        "DEBUG": "False",
        "LOG_LEVEL": args.log_level,
        "GOOGLE_CLOUD_PROJECT": project,
        "GOOGLE_CLOUD_QUOTA_PROJECT": args.quota_project or project,
        "GOOGLE_CLOUD_LOCATION": args.location,
        "VERTEX_AI_LOCATION": args.vertex_location or args.location,
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "LLM_MODEL": args.model,
        "SEARCH_MODEL": args.search_model or args.model,
        "SEARCH_CACHE_BACKEND": "none",
        "SEARCH_QPS": str(args.qps),
        "SEARCH_QPS_BURST": str(args.qps_burst),
        "SEARCH_CONCURRENCY_LIMIT": str(args.concurrency),
        "SEARCH_TIMEOUT_SECONDS": str(args.query_timeout),
        "SEARCH_QUERY_RETRY_ATTEMPTS": str(args.query_retry_attempts),
        "SEARCH_STEP_TIMEOUT_SECONDS": str(args.step_timeout),
        "SEARCH_MIN_SUCCESS_RATE": str(args.min_success_rate),
        "GENAI_HTTP_TIMEOUT_SECONDS": str(args.http_timeout),
    }
    os.environ.update(live)

    live["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds)
    live["service_account_email"] = sa.get("client_email", "?")
    live["ca_bundle"] = ca_bundle
    return live


def slugify(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")


def resolve_out_dir(args: argparse.Namespace, default_leaf: str) -> Path:
    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "out" / "local-e2e" / default_leaf / slugify(args.company)
    )
    out.mkdir(parents=True, exist_ok=True)
    return out


def plain(value: Any) -> Any:
    """Best-effort JSON-safe view of dataclasses / mappings / sequences."""
    if is_dataclass(value) and not isinstance(value, type):
        return {k: plain(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [plain(v) for v in value]
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return repr(value)


# ---------------------------------------------------------------------------
# Injected collaborators
# ---------------------------------------------------------------------------


class InMemorySearchCache:
    """Duck-typed stand-in for RedisSearchCacheRepository.

    Implements only the two coroutines SearchExecutor actually calls.
    Optionally persisted to a JSON file so a rerun of the same company
    costs nothing -- which doubles as the observable proof of the
    cache-as-you-go fix: after a step-level timeout the file still holds
    every query that completed.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._store: dict[str, Any] = {}
        self.hits = 0
        self.misses = 0
        self.writes = 0
        if path is not None and path.is_file():
            try:
                self._store = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"  ! could not read cache file {path}: {exc}")

    @staticmethod
    def _key(company_name: str, query: str) -> str:
        return f"{company_name.strip().lower()}||{query.strip()}"

    async def async_get_search(
        self, company_name: str, query: str
    ) -> dict[str, Any] | None:
        value = self._store.get(self._key(company_name, query))
        if value is None:
            self.misses += 1
            return None
        self.hits += 1
        return value

    async def async_set_search(
        self,
        company_name: str,
        query: str,
        results: Any,
        domain: str | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._store[self._key(company_name, query)] = {
            "results": results,
            "domain": domain,
            "cached_at": datetime.now(UTC).isoformat(),
        }
        self.writes += 1
        self.flush()

    def flush(self) -> None:
        """Persist immediately, mirroring _run_and_cache's write-through.

        Written per query on purpose: if the step is cancelled at its
        deadline, whatever finished is already on disk.
        """
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._store, indent=2), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - best effort
            print(f"  ! cache flush failed: {exc}")

    def stats(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "writes": self.writes,
            "entries_persisted": len(self._store),
            "file": str(self._path) if self._path else None,
        }


class LocalFileStore:
    """Filesystem stand-in for GCSRepository.

    Mirrors the real blob layout under a local root so the artifact tree
    looks exactly like a job's GCS prefix:

        <root>/<parent_folder>/<job_id>/raw_data.json
        <root>/<parent_folder>/<job_id>/final_report.md
        <root>/<parent_folder>/<job_id>/final_report.pdf
        <root>/<parent_folder>/<job_id>/evaluation.json
        <root>/<parent_folder>/<job_id>/artifacts/<agent>_output.json

    Only the five upload_* methods the worker path calls are implemented.
    """

    def __init__(self, root: Path, parent_folder: str = "salesagent_response") -> None:
        self._root = root
        self._folder = parent_folder
        self.written: list[dict[str, Any]] = []

    def _write(self, request_id: str, rel: str, payload: str | bytes) -> str:
        target = self._root / self._folder / request_id / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, bytes):
            target.write_bytes(payload)
        else:
            target.write_text(payload, encoding="utf-8")
        size = target.stat().st_size
        self.written.append({"path": str(target), "bytes": size})
        print(f"  [store] {self._folder}/{request_id}/{rel}  ({size:,} bytes)")
        return target.resolve().as_uri()

    def upload_json(self, request_id: str, data: dict[str, Any]) -> str:
        return self._write(
            request_id, "raw_data.json", json.dumps(plain(data), indent=2)
        )

    def upload_markdown(self, request_id: str, content: str) -> str:
        return self._write(request_id, "final_report.md", content)

    def upload_pdf(self, request_id: str, pdf_bytes: bytes) -> str:
        return self._write(request_id, "final_report.pdf", pdf_bytes)

    def upload_agent_artifact(
        self, session_id: str, agent_name: str, content: str
    ) -> str:
        return self._write(
            session_id, f"artifacts/{agent_name.lower()}_output.json", content
        )

    def upload_evaluation(
        self, request_id: str, evaluation_data: dict[str, Any]
    ) -> str:
        return self._write(
            request_id, "evaluation.json", json.dumps(plain(evaluation_data), indent=2)
        )


class RecordingBigQuery:
    """Stand-in for BigQueryRepository that records instead of writing.

    Keeps one in-memory row per job so ResearchTaskHandler's idempotency
    guard (get_status -> TERMINAL_STATUSES) and its closing status read
    behave exactly as they do against the real table. update_status calls
    are echoed live -- that stream is what the API's polling endpoint
    would show a user.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.rows: dict[str, dict[str, Any]] = {}
        self.cost_attribution: list[dict[str, Any]] = []
        self.telemetry: list[dict[str, Any]] = []
        self._t0 = time.monotonic()

    def _record(self, method: str, **payload: Any) -> None:
        self.calls.append(
            {
                "at_seconds": round(time.monotonic() - self._t0, 2),
                "method": method,
                **plain(payload),
            }
        )

    def create_request(
        self, job_id: str, company_name: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        self.rows[job_id] = {
            "job_execution_id": job_id,
            "company_name": company_name,
            "status": "PENDING",
            "metadata": metadata or {},
        }
        self._record(
            "create_request",
            job_id=job_id,
            company_name=company_name,
            metadata=metadata,
        )
        return True

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        return self.rows.get(job_id)

    def update_status(
        self,
        job_id: str,
        status: str | None,
        gcs_uri: str | None = None,
        error: str | None = None,
        progress: int | None = None,
        current_step: str | None = None,
        metadata_update: dict | None = None,
    ) -> bool:
        row = self.rows.setdefault(job_id, {"job_execution_id": job_id})
        if status:
            row["status"] = status
        for key, value in (
            ("gcs_uri", gcs_uri),
            ("error", error),
            ("progress", progress),
            ("current_step", current_step),
        ):
            if value is not None:
                row[key] = value
        if metadata_update:
            row.setdefault("metadata", {}).update(metadata_update)

        self._record(
            "update_status",
            job_id=job_id,
            status=status,
            gcs_uri=gcs_uri,
            error=error,
            progress=progress,
            current_step=current_step,
            metadata_update=metadata_update,
        )
        shown = status or row.get("status", "?")
        pct = f"{progress:>3}%" if progress is not None else "    "
        print(f"  [status] {pct} {shown:<11} {current_step or ''}")
        return True

    def insert_cost_attribution(self, **kwargs: Any) -> bool:
        self.cost_attribution.append(plain(kwargs))
        self._record("insert_cost_attribution", **kwargs)
        print(
            f"  [cost]   tokens={kwargs.get('total_tokens')} "
            f"searches={kwargs.get('search_count')} "
            f"total=${kwargs.get('total_cost_usd')}"
        )
        return True

    def insert_agent_telemetry_batch(self, records: list[dict[str, Any]]) -> bool:
        self.telemetry.extend(plain(records))
        self._record("insert_agent_telemetry_batch", record_count=len(records))
        print(f"  [telem]  {len(records)} agent telemetry rows")
        return True

    def summary(self) -> dict[str, Any]:
        return {
            "call_count": len(self.calls),
            "calls": self.calls,
            "final_rows": plain(self.rows),
            "cost_attribution": self.cost_attribution,
            "agent_telemetry": self.telemetry,
        }


class RecordingFirestore:
    """Stand-in for FirestoreSearchCacheRepository's write path."""

    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []

    def insert_search_query_batch(self, records: list[dict[str, Any]]) -> bool:
        self.batches.append(plain(records))
        print(f"  [fstore] {len(records)} search-query rows")
        return True

    @property
    def row_count(self) -> int:
        return sum(len(b) for b in self.batches)


def build_console_observer(observer_base: type) -> Any:
    """Build a ConsoleObserver subclass of the real Observer ABC.

    A factory so the `src` import stays below bootstrap_env().
    """

    class ConsoleObserver(observer_base):  # type: ignore[misc, valid-type]
        """Replaces ProgressObserver (BigQuery) + TracingObserver (OTel)."""

        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []
            self.usage: dict[str, dict[str, int]] = {}
            self._t0 = time.monotonic()

        def _log(self, line: str) -> None:
            print(f"  [{time.monotonic() - self._t0:7.2f}s] {line}", flush=True)

        def on_start(self, agent_name: str, attempt: int) -> None:
            self.events.append(
                {"event": "start", "agent": agent_name, "attempt": attempt}
            )
            self._log(f"START   {agent_name} (attempt {attempt})")

        def on_retry(
            self, agent_name: str, attempt: int, kind: Any, delay: float
        ) -> None:
            self.events.append(
                {
                    "event": "retry",
                    "agent": agent_name,
                    "attempt": attempt,
                    "kind": str(kind),
                    "delay_seconds": round(delay, 2),
                }
            )
            self._log(
                f"RETRY   {agent_name} attempt {attempt} kind={kind} in {delay:.2f}s"
            )

        def on_success(self, agent_name: str, attempt: int, seconds: float) -> None:
            self.events.append(
                {
                    "event": "success",
                    "agent": agent_name,
                    "attempt": attempt,
                    "seconds": round(seconds, 2),
                }
            )
            self._log(f"OK      {agent_name} in {seconds:.2f}s (attempt {attempt})")

        def on_failure(
            self, agent_name: str, attempt: int, kind: Any, exc: BaseException
        ) -> None:
            self.events.append(
                {
                    "event": "failure",
                    "agent": agent_name,
                    "attempt": attempt,
                    "kind": str(kind),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            self._log(
                f"FAIL    {agent_name} attempt {attempt} kind={kind} "
                f"{type(exc).__name__}: {exc}"
            )

        def on_usage(
            self, agent_name: str, model: str, input_tokens: int, output_tokens: int
        ) -> None:
            bucket = self.usage.setdefault(model, {"input": 0, "output": 0})
            bucket["input"] += input_tokens
            bucket["output"] += output_tokens
            self._log(
                f"USAGE   {agent_name} {model} in={input_tokens} out={output_tokens}"
            )

    return ConsoleObserver()


# ---------------------------------------------------------------------------
# Fault injection (used by local_research_e2e.py)
# ---------------------------------------------------------------------------


def install_hang_fault(searcher: Any, hang_count: int) -> dict[str, Any]:
    """Make the first *hang_count* distinct queries hang forever.

    This is the exact prod failure mode: _search_once never returns.
    Before the fix, _run_one awaited it bare and one such query held a
    semaphore slot until the step-level deadline killed everything. After
    the fix, each attempt is bounded by query_retry.timeout, so the hang
    costs that query its retries and nothing else.

    Wrapping _search_once (not the genai client) keeps the hung queries
    free -- no request is ever issued for them.
    """
    stats: dict[str, Any] = {"hung_queries": [], "hang_events": 0}
    original = searcher._search_once
    hung: set[str] = set()

    async def patched(company: str, query: Any) -> Any:
        if query.text in hung or len(hung) < hang_count:
            if query.text not in hung:
                hung.add(query.text)
                stats["hung_queries"].append(
                    {"domain": query.domain, "text": query.text}
                )
            stats["hang_events"] += 1
            print(f"  [fault] hanging: [{query.domain}] {query.text!r}", flush=True)
            await asyncio.Event().wait()  # never set
        return await original(company, query)

    searcher._search_once = patched  # type: ignore[method-assign]
    return stats


def break_query_timeout(searcher: Any) -> None:
    """Restore the pre-fix behavior so the failure can be reproduced.

    asyncio.wait_for(coro, timeout=None) waits forever -- exactly the
    semantics of the bare `await self._search_once(...)` this replaced.
    """
    searcher._query_retry.timeout = None


def truncate_plan_after_planner(planner: Any, max_queries: int) -> None:
    """Cap the planner's output so a full run can be exercised cheaply.

    Wraps QueryPlanner.run rather than editing the pipeline, so the real
    planner still makes its real LLM call and everything downstream sees
    an ordinary QueryPlan -- just a shorter one.
    """
    from src.worker.agents.models import QueryPlan

    original = planner.run

    async def patched(request: Any, obs: Any) -> Any:
        plan = await original(request, obs)
        if max_queries and len(plan.queries) > max_queries:
            print(
                f"  (truncating plan {len(plan.queries)} -> {max_queries} queries)",
                flush=True,
            )
            return QueryPlan(company=plan.company, queries=plan.queries[:max_queries])
        return plan

    planner.run = patched  # type: ignore[method-assign]
