#!/usr/bin/env python
"""Run the project's real research core loop locally, with no server.

This drives `ResearchPipeline.run()` -- the exact coroutine the Cloud
Tasks worker route ends up calling -- so every LLM call is the real one,
made through the real agents, prompts, retry policies, timeouts and
guardrails:

    ResearchPipeline.run()
      QueryPlanner        LLM: generate candidate queries + BM25-select
      SearchExecutor      LLM: grounded Google Search per query (QPS-gated)
      AlignmentAnalyst    LLM: map findings onto the Colt catalog
      ReportCompiler      LLM: compile the Markdown brief, then
                          OutputGuardrail + Bm25Verifier gate it

Nothing above the storage line is mocked. What this script deliberately
leaves out is the infrastructure the job normally hangs off: no FastAPI
app, no Cloud Tasks, no BigQuery job row, no GCS upload, no PDF render,
no LLM-judge evaluation, no cost attribution and no OpenTelemetry
exporter. The compiled report is written straight to a local directory
instead.

Two seams are injected, both at boundaries the production code already
exposes:

    RedisSearchCacheRepository -> InMemorySearchCache   (Memorystore is
        on a private VPC IP and is unreachable off-cluster)
    ProgressObserver/TracingObserver -> _LogObserver    (no BigQuery
        status rows, no OTel spans; the per-agent timeline is logged)

Logging is the service's own: `setup_logging()` from
src.shared.logging_config is called exactly as src/worker/main.py calls
it at import time, at LOG_LEVEL=DEBUG by default, so the terminal shows
the same records (agent lifecycle, [SearchExecutor] per-query lines,
[ReportCompiler], [OutputGuardrail], google_adk / google_genai debug)
that appear when the worker is up. They are mirrored to <out>/app.log
via settings.LOG_FILE.

Usage
-----
  # Full run (~30 queries), repo service_account.json, global endpoint
  python scripts/local_core_loop.py -c "Societe Generale"

  # Cheap run: 6 search queries, everything else unchanged
  python scripts/local_core_loop.py -c "Societe Generale" --max-queries 6

  # Planner -> search -> compiler only, no alignment LLM call
  python scripts/local_core_loop.py -c "Societe Generale" --skip-alignment

  # Same log shape as a deployed worker (Cloud Logging structured JSON)
  python scripts/local_core_loop.py -c "Societe Generale" --log-format json

Notes
-----
* --location/--vertex-location default to `global`: with the repo's
  service_account.json, gemini-3.5-flash is only served from the global
  Vertex AI endpoint in that project -- regional endpoints 404 for it.
* --skip-alignment still runs ReportCompiler, but its CompilerInput
  carries an empty ColtAlignment, so sections 8 and 11 of the brief have
  no source material. Leave it off for a representative report.
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
sys.path.insert(0, str(REPO_ROOT))

# ---------------------------------------------------------------------------
# Environment bootstrap
#
# src.shared.config builds a module-level `settings` singleton at import
# time, so every env var has to be in place before the first `src.*`
# import. This script deliberately does NOT read .env / .env.worker.local:
# those point at a private-IP Redis and a live Cloud Trace OTLP endpoint --
# exactly what a local, server-free run exists to avoid.
# ---------------------------------------------------------------------------

# Settings fields the model requires but that belong to subsystems this
# script never constructs (no BigQuery dataset, no GCS bucket, no app).
_UNUSED_PLACEHOLDERS: dict[str, str] = {
    "APP_NAME": "sales-agent-local-core-loop",
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
    "SECRET_KEY": "local-core-loop-not-a-real-secret",
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
        bundle = REPO_ROOT / "out" / "local-core-loop" / "_ca-bundle.pem"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        blocks = [Path(certifi.where()).read_text(encoding="utf-8"), *pems]
        bundle.write_text("\n".join(blocks), encoding="utf-8")

    os.environ["SSL_CERT_FILE"] = str(bundle)
    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    os.environ["GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"] = str(bundle)
    return str(bundle)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-c", "--company", default="Societe Generale")

    # --- credentials / region / model ------------------------------------
    p.add_argument(
        "--credentials",
        default="service_account.json",
        help="Service-account JSON key (default: ./service_account.json).",
    )
    p.add_argument("--project", default="", help="Overrides the key's project_id.")
    p.add_argument("--quota-project", default="")
    p.add_argument(
        "--location",
        default="global",
        help="Project location (GOOGLE_CLOUD_LOCATION). Default: global.",
    )
    p.add_argument(
        "--vertex-location",
        default="",
        help="Gemini inference location (VERTEX_AI_LOCATION); defaults to "
        "--location. The repo's service-account key needs 'global' -- "
        "gemini-3.5-flash is not served from that project's regional "
        "endpoints.",
    )
    p.add_argument("--model", default="gemini-3.5-flash")
    p.add_argument("--search-model", default="", help="Defaults to --model.")

    # --- what to run -----------------------------------------------------
    p.add_argument(
        "--max-queries",
        type=int,
        default=0,
        help="Cap the planner's QueryPlan at N queries (0 = all ~30). "
        "Every later step still runs in full.",
    )
    p.add_argument(
        "--skip-alignment",
        action="store_true",
        help="Run only QueryPlanner -> SearchExecutor -> ReportCompiler. "
        "The compiler then receives an empty ColtAlignment, so report "
        "sections 8 and 11 have no source material.",
    )
    p.add_argument(
        "--job-id", default="", help="Job id to run under (default: local-<epoch>)."
    )

    # --- search tuning (mirrors the SEARCH_* settings) -------------------
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

    # --- local plumbing --------------------------------------------------
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
        "--out",
        default="",
        help="Directory the report is written to "
        "(default: out/local-core-loop/<company-slug>).",
    )
    p.add_argument("--log-level", default="DEBUG", help="LOG_LEVEL. Default: DEBUG.")
    p.add_argument(
        "--log-format",
        choices=("text", "json"),
        default="text",
        help="'text' is the local worker's human-readable format "
        "(settings.DEBUG=True); 'json' is the Cloud Logging structured "
        "format a deployed worker emits. Default: text.",
    )
    return p.parse_args(argv)


def slugify(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")


def resolve_out_dir(args: argparse.Namespace) -> Path:
    out = (
        Path(args.out)
        if args.out
        else REPO_ROOT / "out" / "local-core-loop" / slugify(args.company)
    )
    out.mkdir(parents=True, exist_ok=True)
    return out


def bootstrap_env(args: argparse.Namespace, out_dir: Path) -> dict[str, str]:
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

    # Do not let a repo .env / /secrets/.env leak deployed config in.
    os.environ["DOTENV_DISABLE"] = "1"

    # Kill every telemetry path. Nothing here calls otel_setup, so the
    # OTel API hands out no-op tracers and the @traced decorators become
    # free no-ops; these vars make that explicit rather than incidental.
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
        # settings.DEBUG selects the log formatter: True -> the local
        # worker's human-readable line format, False -> the Cloud Logging
        # JSON a deployed worker emits (logging_config._build_formatter).
        "DEBUG": "True" if args.log_format == "text" else "False",
        "LOG_LEVEL": args.log_level,
        "LOG_FILE": str(out_dir / "app.log"),
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
    }
    os.environ.update(live)

    live["GOOGLE_APPLICATION_CREDENTIALS"] = str(creds)
    live["service_account_email"] = sa.get("client_email", "?")
    live["ca_bundle"] = ca_bundle
    return live


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
    costs nothing; written through on every set, so whatever completed
    before a step-level deadline is already on disk.
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


def build_log_observer(observer_base: type, logger: Any) -> Any:
    """Observer that logs the per-agent timeline through the app logger.

    Stands in for the pair ResearchJobRunner normally composes:
    ProgressObserver (writes progress rows to BigQuery) and
    TracingObserver (opens OTel spans). A factory so the `src` imports
    stay below bootstrap_env().
    """

    class _LogObserver(observer_base):  # type: ignore[misc, valid-type]
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []
            self.usage: dict[str, dict[str, int]] = {}
            self._t0 = time.monotonic()

        def _elapsed(self) -> str:
            return f"{time.monotonic() - self._t0:7.2f}s"

        def on_start(self, agent_name: str, attempt: int) -> None:
            self.events.append(
                {"event": "start", "agent": agent_name, "attempt": attempt}
            )
            logger.info(
                f"[CoreLoop] [{self._elapsed()}] START   {agent_name} "
                f"(attempt {attempt})"
            )

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
            logger.warning(
                f"[CoreLoop] [{self._elapsed()}] RETRY   {agent_name} "
                f"attempt {attempt} kind={kind} in {delay:.2f}s"
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
            logger.info(
                f"[CoreLoop] [{self._elapsed()}] OK      {agent_name} in "
                f"{seconds:.2f}s (attempt {attempt})"
            )

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
            logger.error(
                f"[CoreLoop] [{self._elapsed()}] FAIL    {agent_name} "
                f"attempt {attempt} kind={kind} {type(exc).__name__}: {exc}"
            )

        def on_usage(
            self, agent_name: str, model: str, input_tokens: int, output_tokens: int
        ) -> None:
            bucket = self.usage.setdefault(model, {"input": 0, "output": 0})
            bucket["input"] += input_tokens
            bucket["output"] += output_tokens
            logger.info(
                f"[CoreLoop] [{self._elapsed()}] USAGE   {agent_name} {model} "
                f"in={input_tokens} out={output_tokens}"
            )

    return _LogObserver()


def truncate_plan_after_planner(planner: Any, max_queries: int, logger: Any) -> None:
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
            logger.warning(
                f"[CoreLoop] truncating query plan {len(plan.queries)} -> "
                f"{max_queries} queries (--max-queries)"
            )
            return QueryPlan(company=plan.company, queries=plan.queries[:max_queries])
        return plan

    planner.run = patched  # type: ignore[method-assign]


def install_skipped_alignment(pipeline: Any, logger: Any) -> None:
    """Replace AlignmentAnalyst with an honest no-op (--skip-alignment).

    ReportCompiler's input contract is CompilerInput(findings, alignment),
    so the step cannot simply be dropped: the compiler still runs, with an
    empty ColtAlignment, and its prompt renders "(no alignment mappings
    available)" for sections 8 and 11.
    """
    from src.worker.agents.models import ColtAlignment

    class _SkippedAlignment:
        name = "AlignmentAnalyst"

        async def run(self, findings: Any, obs: Any) -> ColtAlignment:
            logger.warning(
                "[CoreLoop] AlignmentAnalyst SKIPPED (--skip-alignment): "
                "ReportCompiler receives an empty ColtAlignment, so report "
                "sections 8 (Colt Alignment Table) and 11 (Strategic "
                "Opportunity) will have no source material."
            )
            return ColtAlignment(mappings=(), opportunity_summary="")

    pipeline._analyst = _SkippedAlignment()


def preflight_assets(settings: Any, logger: Any) -> None:
    """Mirror the worker lifespan's mounted-asset preflight, non-fatally.

    src/worker/main.py aborts startup when the pricing catalog or the Colt
    product catalog PDF is missing. Locally the Colt catalog has an
    embedded fallback (see gcs_pdf_loader), and the pricing catalog is
    only read for cost attribution, which this script does not do -- so a
    miss is logged and the run continues.
    """
    logger.info("Validating required mounted assets during startup lifecycle...")
    try:
        mounted = settings.validate_mounted_assets()
        logger.info(
            "Mounted assets verified: %s", {k: str(v) for k, v in mounted.items()}
        )
    except Exception as exc:
        logger.warning(
            f"Mounted asset preflight incomplete ({exc}); continuing -- the "
            "Colt catalog falls back to the embedded text and no cost "
            "attribution runs here."
        )

    from src.worker.agents.tools.gcs_pdf_loader import load_mounted_colt_catalog_pdf

    catalog_text = load_mounted_colt_catalog_pdf()
    logger.info(
        "Colt product catalog: %d characters extracted from the mounted PDF",
        len(catalog_text) if catalog_text else 0,
    )


async def run(args: argparse.Namespace, out_dir: Path, live: dict[str, str]) -> int:
    # Imports happen here, after bootstrap_env() froze the config.
    from src.shared.config import settings
    from src.shared.logging_config import logger, setup_logging
    from src.worker.agents.base import AgentError
    from src.worker.agents.models import ResearchRequest
    from src.worker.observers import Observer
    from src.worker.services.formatting import clean_markdown_report

    setup_logging()

    job_id = args.job_id or f"local-{int(time.time())}"
    cache_path = (
        None
        if args.no_cache
        else (
            Path(args.cache_file) if args.cache_file else out_dir / "search-cache.json"
        )
    )
    cache = InMemorySearchCache(cache_path)
    observer = build_log_observer(Observer, logger)

    print("=" * 78)
    print(f"Local core loop  |  company={args.company!r}  job_id={job_id}")
    print("=" * 78)
    for key in sorted(live):
        print(f"  {key:34} {live[key]}")
    print(f"  {'max queries':34} {args.max_queries or 'all (~30)'}")
    print(f"  {'alignment step':34} {'SKIPPED' if args.skip_alignment else 'ON'}")
    print(f"  {'output dir':34} {out_dir}")
    print(f"  {'cache file':34} {cache_path or '(disabled)'}")
    print("-" * 78, flush=True)

    preflight_assets(settings, logger)

    # --- assemble the real object graph ----------------------------------
    # build_research_pipeline() is the production factory; the only thing
    # swapped is the search cache it constructs, since Memorystore lives
    # on a private VPC IP that is unreachable from a laptop.
    import src.worker.dependencies as worker_deps

    worker_deps.RedisSearchCacheRepository = lambda: cache  # type: ignore[assignment]
    pipeline = worker_deps.build_research_pipeline()

    if args.max_queries:
        truncate_plan_after_planner(pipeline._planner, args.max_queries, logger)
    if args.skip_alignment:
        install_skipped_alignment(pipeline, logger)

    summary: dict[str, Any] = {
        "job_id": job_id,
        "company": args.company,
        "started_at": datetime.now(UTC).isoformat(),
        "settings": live,
        "options": {
            "max_queries": args.max_queries,
            "skip_alignment": args.skip_alignment,
        },
    }
    wall = time.monotonic()
    exit_code = 0
    result = None

    logger.info(
        f"[Pipeline] Starting research job job_id={job_id} company={args.company!r}"
    )
    try:
        result = await pipeline.run(
            ResearchRequest(job_id=job_id, company=args.company), observer
        )
        summary["status"] = "COMPLETED"
    except AgentError as exc:
        exit_code = 1
        summary["status"] = "FAILED"
        summary["failure"] = {
            "agent": exc.agent_name,
            "kind": str(exc.kind),
            "attempts": exc.attempts,
            "message": str(exc),
            "cause": f"{type(exc.cause).__name__}: {exc.cause}" if exc.cause else None,
        }
        logger.error(
            f"[Pipeline] FAILED agent={exc.agent_name} kind={exc.kind} "
            f"attempts={exc.attempts}: {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - a local harness reports everything
        exit_code = 1
        summary["status"] = "ERROR"
        summary["failure"] = {"message": f"{type(exc).__name__}: {exc}"}
        logger.exception(f"[Pipeline] UNEXPECTED ERROR: {type(exc).__name__}: {exc}")

    # --- write what the loop produced ------------------------------------
    report_path: Path | None = None
    if result is not None:
        # clean_markdown_report is what ResearchJobRunner applies to the
        # report before it is uploaded, so the local file matches the
        # artifact a real job would store.
        markdown = clean_markdown_report(result.report.markdown)
        report_path = out_dir / f"{slugify(args.company)}_report.md"
        report_path.write_text(markdown, encoding="utf-8")
        logger.info(
            f"[CoreLoop] report written: {report_path} ({len(markdown):,} chars)"
        )

        findings = result.findings
        summary["report"] = {
            "path": str(report_path),
            "chars": len(markdown),
            "validation_status": result.report.validation_status,
            "validation_violations": plain(result.report.validation_violations),
        }
        summary["search"] = {
            "executed": findings.executed,
            "failed_count": len(findings.failed),
            "success_rate": round(findings.success_rate, 4),
            "populated_domains": findings.populated_domain_count,
            "evidence_urls": len(findings.all_evidence()),
            "failed_queries": list(findings.failed),
            "domains": {
                key: {
                    "domain": finding.domain,
                    "chars": len(finding.content),
                    "evidence_urls": len(finding.evidence),
                }
                for key, finding in findings.domains.items()
            },
        }
        summary["alignment"] = {
            "mappings": len(result.alignment.mappings),
            "hooks": len(result.alignment.hooks),
            "opportunity_summary_chars": len(result.alignment.opportunity_summary),
        }
        summary["token_usage_by_model"] = plain(result.token_usage_by_model)
        summary["agent_telemetry_records"] = plain(result.telemetry_records)

    summary["wall_seconds"] = round(time.monotonic() - wall, 2)
    summary["cache"] = cache.stats()
    summary["agent_events"] = observer.events
    summary["observed_token_usage"] = observer.usage

    summary_path = out_dir / "run-summary.json"
    summary_path.write_text(json.dumps(plain(summary), indent=2), encoding="utf-8")

    # --- report ----------------------------------------------------------
    tokens = sum(v["input"] + v["output"] for v in observer.usage.values())
    c = summary["cache"]
    print("\n" + "=" * 78)
    print(f"  status             {summary.get('status')}")
    print(f"  wall time          {summary['wall_seconds']}s")
    if "search" in summary:
        s = summary["search"]
        print(
            f"  search             {s['executed']} succeeded / "
            f"{s['failed_count']} failed  ({s['success_rate']:.0%}), "
            f"{s['populated_domains']}/12 domains, {s['evidence_urls']} evidence URLs"
        )
    if "report" in summary:
        r = summary["report"]
        print(f"  report             {r['chars']:,} chars, {r['validation_status']}")
        print(f"  report file        {r['path']}")
    if "failure" in summary:
        print(f"  failure            {summary['failure'].get('message')}")
    print(f"  tokens (observed)  {tokens:,} across {len(observer.usage)} model(s)")
    print(
        f"  search cache       {c['hits']} hits / {c['misses']} misses / "
        f"{c['writes']} writes"
    )
    print(f"  app log            {settings.app_log_path}")
    print(f"  run summary        {summary_path}")
    print("=" * 78)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    out_dir = resolve_out_dir(args)
    live = bootstrap_env(args, out_dir)
    return asyncio.run(run(args, out_dir, live))


if __name__ == "__main__":
    raise SystemExit(main())
