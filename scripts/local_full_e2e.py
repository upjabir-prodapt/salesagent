#!/usr/bin/env python
"""Run the entire worker research loop locally, end to end, with no server.

This drives the real `ResearchTaskHandler.handle()` -- the exact function
the Cloud Tasks HTTP route calls -- so every stage of the core loop runs
for real:

    ResearchTaskHandler.handle()          idempotency guard, job lookup
      -> ResearchJobRunner.run()          PROCESSING -> terminal state
        -> ResearchPipeline.run()
             QueryPlanner                 LLM: generate + BM25-select queries
             SearchExecutor               LLM: grounded Google Search x N
             AlignmentAnalyst             LLM: map findings to Colt catalog
             ReportCompiler               LLM: compile the Markdown brief,
                                          OutputGuardrail + Bm25Verifier
        -> clean_markdown_report / calculate_metrics / reconcile_cost
        -> ResearchArtifactService        report + raw state + per-agent artifacts
        -> ResearchFinalizationService
             PDF render                   markdown -> HTML -> PDF
             EvaluationService            LLM judge (Section A) + automated
                                          metrics (Section B)
             cost attribution
             search-query log flush
             agent-telemetry flush
        -> build_completion_metadata      COMPLETED

Nothing is mocked above the storage line. Four collaborators are injected
at the constructor seams the production code already exposes -- see
_local_harness.py for what each replaces and why:

    RedisSearchCacheRepository      -> InMemorySearchCache
    GCSRepository                   -> LocalFileStore   (writes to disk)
    BigQueryRepository              -> RecordingBigQuery (records calls)
    FirestoreSearchCacheRepository  -> RecordingFirestore

No OpenTelemetry exporter is configured, so TracingObserver and the
@traced decorators resolve to no-op tracers and emit nothing.

Usage
-----
  # Full run, all ~30 queries
  python scripts/local_full_e2e.py -c "Societe Generale" --vertex-location global

  # Cheap full run: 6 queries, still exercises report + eval + finalization
  python scripts/local_full_e2e.py -c "Societe Generale" --vertex-location global --max-queries 6

  # Skip the LLM judge (Section A) when you only care about the report
  python scripts/local_full_e2e.py -c "Societe Generale" --vertex-location global --max-queries 6 --skip-eval

Note: --vertex-location global is required with the repo's
service_account.json -- gemini-3.5-flash is only served from the global
endpoint in that project; regional endpoints 404 for it.

PDF: rendering needs WeasyPrint's native GTK libraries, which are absent
on a stock Windows box (`cannot load library 'libgobject-2.0-0'`). The
loop treats that as a non-fatal side-op failure exactly as it would in
prod, and this script additionally writes report.html using the same
markdown extensions and CSS so the rendered layout is still inspectable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _local_harness import (  # noqa: E402
    InMemorySearchCache,
    LocalFileStore,
    RecordingBigQuery,
    RecordingFirestore,
    add_common_args,
    bootstrap_env,
    build_console_observer,
    plain,
    resolve_out_dir,
    truncate_plan_after_planner,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_args(p)
    p.add_argument(
        "--max-queries",
        type=int,
        default=0,
        help="Cap the planner's QueryPlan at N queries (0 = all ~30). "
        "Every later stage still runs in full.",
    )
    p.add_argument(
        "--job-id",
        default="",
        help="Job id to run under (default: local-full-<epoch>).",
    )
    p.add_argument(
        "--skip-eval",
        action="store_true",
        help="Skip EvaluationService (the LLM judge is the slowest side op).",
    )
    p.add_argument(
        "--skip-pdf",
        action="store_true",
        help="Skip PDF rendering instead of letting it fail on a box "
        "without WeasyPrint's native libraries.",
    )
    return p.parse_args(argv)


def write_report_html(out_dir: Path, markdown_text: str) -> Path | None:
    """Render the report the way generate_pdf() would, minus the PDF step.

    A script-side convenience, not part of the loop: it uses the very
    same markdown extensions and the service's _REPORT_PDF_CSS, so when
    WeasyPrint cannot load its native libraries you can still see what
    the PDF would have looked like.
    """
    try:
        import markdown

        from src.worker.services.finalization_service import _REPORT_PDF_CSS

        body = markdown.markdown(
            markdown_text, extensions=["tables", "fenced_code", "nl2br"]
        )
        target = out_dir / "report.html"
        target.write_text(
            "<!DOCTYPE html>\n<html>\n<head><meta charset='utf-8'>"
            f"<style>{_REPORT_PDF_CSS}</style></head>\n<body>{body}</body>\n</html>",
            encoding="utf-8",
        )
        return target
    except Exception as exc:  # pragma: no cover - convenience only
        print(f"  ! report.html render skipped: {exc}")
        return None


async def run(args: argparse.Namespace, live_settings: dict[str, str]) -> int:
    # Imports happen here, after bootstrap_env() froze the config.
    from src.shared.schemas.tasks import ResearchTaskPayload
    from src.worker.agents.base import AgentError
    from src.worker.api.handlers import ResearchTaskHandler
    from src.worker.dependencies import build_research_pipeline
    from src.worker.observers import Observer
    from src.worker.services.artifacts import ResearchArtifactService
    from src.worker.services.finalization_service import ResearchFinalizationService
    from src.worker.services.job_runner import ResearchJobRunner

    out_dir = resolve_out_dir(args, "full")
    store_root = out_dir / "gcs"
    cache_path = (
        None
        if args.no_cache
        else (
            Path(args.cache_file) if args.cache_file else out_dir / "search-cache.json"
        )
    )

    cache = InMemorySearchCache(cache_path)
    store = LocalFileStore(store_root)
    bq = RecordingBigQuery()
    firestore = RecordingFirestore()
    console = build_console_observer(Observer)

    # --- assemble the real object graph, production constructors ---------
    pipeline = build_research_pipeline(cache_repo=cache)
    if args.max_queries:
        truncate_plan_after_planner(pipeline._planner, args.max_queries)

    evaluation_service: Any = None
    if args.skip_eval:

        class _SkippedEvaluation:
            """Honest no-op: records that evaluation was deliberately skipped."""

            async def evaluate(self, request_id, final_report, session_state):
                return {"skipped": True, "reason": "--skip-eval"}

        evaluation_service = _SkippedEvaluation()

    finalization = ResearchFinalizationService(
        bigquery_repository=bq,
        gcs_repository=store,
        evaluation_service=evaluation_service,  # None -> the real EvaluationService
        search_cache_repository=firestore,
    )
    if args.skip_pdf:

        def _skip_pdf(_final_report: str) -> bytes:
            raise RuntimeError("PDF rendering skipped via --skip-pdf")

        # Shadows the class's generate_pdf staticmethod for this instance;
        # finalize() reads it as self.generate_pdf.
        finalization.generate_pdf = _skip_pdf

    runner = ResearchJobRunner(
        pipeline=pipeline,
        bigquery_repository=bq,
        artifact_service=ResearchArtifactService(bq, store),
        finalization_service=finalization,
    )
    handler = ResearchTaskHandler(job_runner=runner, bigquery_repository=bq)

    # ResearchJobRunner._run_pipeline builds its observers internally
    # (ProgressObserver + TracingObserver). Swap in the same real
    # ProgressObserver -- now writing to RecordingBigQuery -- plus the
    # ConsoleObserver, so there is a per-agent timeline on stdout and no
    # OTel span machinery at all.
    from src.worker.agents.models import ResearchRequest
    from src.worker.observers import CompositeObserver, ProgressObserver

    async def _run_pipeline_with_console(job_id: str, company_name: str, *, span: Any):
        observer = CompositeObserver(
            [ProgressObserver(job_id, bq.update_status, 4), console]
        )
        return await pipeline.run(
            ResearchRequest(job_id=job_id, company=company_name), observer
        )

    runner._run_pipeline = _run_pipeline_with_console  # type: ignore[method-assign]

    job_id = args.job_id or f"local-full-{int(time.time())}"
    metadata = {
        "username": "local-e2e",
        "user_id": "local-e2e@localhost",
        "business_unit": "local",
        "organization": "local",
    }

    print("=" * 78)
    print(f"Full local E2E  |  company={args.company!r}  job_id={job_id}")
    print("=" * 78)
    for key in sorted(live_settings):
        print(f"  {key:34} {live_settings[key]}")
    print(f"  {'max queries':34} {args.max_queries or 'all (~30)'}")
    print(f"  {'evaluation (LLM judge)':34} {'SKIPPED' if args.skip_eval else 'ON'}")
    print(f"  {'pdf render':34} {'SKIPPED' if args.skip_pdf else 'ON'}")
    print(f"  {'artifact root':34} {store_root}")
    print(f"  {'cache file':34} {cache_path or '(disabled)'}")
    print("-" * 78)

    summary: dict[str, Any] = {
        "job_id": job_id,
        "company": args.company,
        "started_at": datetime.now(UTC).isoformat(),
        "settings": live_settings,
        "options": {
            "max_queries": args.max_queries,
            "skip_eval": args.skip_eval,
            "skip_pdf": args.skip_pdf,
        },
    }
    wall = time.monotonic()
    exit_code = 0

    # The API creates the job row before enqueueing the task; mirror that
    # so the handler's lookup and idempotency guard see a real record.
    bq.create_request(job_id, args.company, metadata)

    try:
        # The exact call the Cloud Tasks HTTP route makes.
        payload = ResearchTaskPayload(
            job_id=job_id, company_name=args.company, metadata=metadata
        )
        result = await handler.handle(payload)
        summary["handler_result"] = result
        summary["status"] = str(result.get("status", "UNKNOWN"))
        if summary["status"] != "COMPLETED":
            exit_code = 1
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
        print(
            f"\n  PIPELINE FAILED: agent={exc.agent_name} kind={exc.kind} "
            f"attempts={exc.attempts}\n  {exc}"
        )
    except Exception as exc:  # noqa: BLE001 - a local harness reports everything
        exit_code = 1
        summary["status"] = "ERROR"
        summary["failure"] = {"message": f"{type(exc).__name__}: {exc}"}
        print(f"\n  UNEXPECTED ERROR: {type(exc).__name__}: {exc}")

    # --- collect everything the loop produced ----------------------------
    report_md = store_root / "salesagent_response" / job_id / "final_report.md"
    if report_md.is_file():
        markdown_text = report_md.read_text(encoding="utf-8")
        summary["report"] = {"chars": len(markdown_text), "path": str(report_md)}
        html = write_report_html(out_dir, markdown_text)
        if html:
            summary["report"]["html"] = str(html)

    eval_json = store_root / "salesagent_response" / job_id / "evaluation.json"
    if eval_json.is_file():
        try:
            data = json.loads(eval_json.read_text(encoding="utf-8"))
            summary["evaluation"] = {
                "final_composite_score": data.get("final_composite_score"),
                "section_a_score": (data.get("section_a") or {}).get("section_a_score"),
                "section_b_score": (data.get("section_b") or {}).get("section_b_score"),
                "skipped": data.get("skipped", False),
                "path": str(eval_json),
            }
        except Exception as exc:
            summary["evaluation"] = {"error": str(exc)}

    final_row = bq.get_status(job_id) or {}
    summary["final_job_row"] = plain(final_row)
    summary["wall_seconds"] = round(time.monotonic() - wall, 2)
    summary["cache"] = cache.stats()
    summary["token_usage_by_model"] = console.usage
    summary["agent_events"] = console.events
    summary["bigquery"] = bq.summary()
    summary["firestore_search_rows"] = firestore.row_count
    summary["artifacts_written"] = store.written

    (out_dir / "run-summary.json").write_text(
        json.dumps(plain(summary), indent=2), encoding="utf-8"
    )

    # --- report ----------------------------------------------------------
    meta = final_row.get("metadata") or {}
    print("\n" + "=" * 78)
    print(f"  job status         {summary.get('status')}")
    print(f"  wall time          {summary['wall_seconds']}s")
    if "report" in summary:
        print(f"  report             {summary['report']['chars']:,} chars")
    if "evaluation" in summary:
        e = summary["evaluation"]
        if e.get("skipped"):
            print("  evaluation         skipped (--skip-eval)")
        else:
            print(
                f"  evaluation         composite={e.get('final_composite_score')} "
                f"(A={e.get('section_a_score')}, B={e.get('section_b_score')})"
            )
    if meta.get("side_op_failures"):
        for op, err in meta["side_op_failures"].items():
            print(f"  side-op FAILED     {op}: {str(err)[:90]}")
    print(f"  pdf available      {meta.get('pdf_available')}")
    tokens = sum(v["input"] + v["output"] for v in console.usage.values())
    print(f"  tokens (observed)  {tokens:,} across {len(console.usage)} model(s)")
    c = summary["cache"]
    print(
        f"  search cache       {c['hits']} hits / {c['misses']} misses / "
        f"{c['writes']} writes"
    )
    print(
        f"  would-be writes    BigQuery {summary['bigquery']['call_count']} calls, "
        f"Firestore {summary['firestore_search_rows']} search rows"
    )
    print(f"  artifacts          {len(store.written)} files under {store_root}")
    print(f"  run summary        {out_dir / 'run-summary.json'}")
    print("=" * 78)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    import asyncio

    args = parse_args(argv)
    live_settings = bootstrap_env(args)
    return asyncio.run(run(args, live_settings))


if __name__ == "__main__":
    raise SystemExit(main())
