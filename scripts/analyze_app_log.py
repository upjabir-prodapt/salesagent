#!/usr/bin/env python3
"""Analyze app.log and agent.log; produce a structured error report."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \| (\w+)\s+\| ([^|]+) \| ([^|]+) \| ([^:]+):(.+)$"
)
RETRY_LINE_RE = re.compile(r"\[Retry\]\s*(.*)")
ERROR_CLASS_RE = re.compile(r"error_class=(\w+)")
AGENT_RE = re.compile(r"agent=(\w+)")
ATTEMPT_RE = re.compile(r"attempt=(\d+)/(\d+)")
JOB_ID_RE = re.compile(r"job_id=([^\s]+)")
SESSION_ID_RE = re.compile(r"session_id=([^\s]+)")
OUTPUT_KEY_RE = re.compile(r"output_key='([^']+)'")

PROD_TRACE_PREFIX = "88c3c885"

FAILURE_RETRY_KEYWORDS = (
    "Agent output failure",
    "Preparing retry",
    "Cold retry",
    "Denied",
    "exhausted",
    "Approved for",
    "Evaluating agent",
    "Fail-fast",
    "Leaf retry",
    "Side operation exhausted",
    "job_id=",
)


@dataclass
class LogEvent:
    line_no: int
    timestamp: str
    level: str
    trace_id: str
    user: str
    logger: str
    message: str

    @property
    def is_test(self) -> bool:
        return self.trace_id.strip() in ("no-trace", "None", "")

    @property
    def is_production(self) -> bool:
        return PROD_TRACE_PREFIX in self.trace_id


@dataclass
class AnalysisResult:
    events: list[LogEvent] = field(default_factory=list)
    tracebacks: list[dict[str, Any]] = field(default_factory=list)


def parse_log_file(path: Path) -> list[LogEvent]:
    events: list[LogEvent] = []
    if not path.exists():
        return events
    with path.open(encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            m = LOG_LINE_RE.match(line.rstrip("\n"))
            if not m:
                continue
            ts, level, trace_id, user, logger, message = m.groups()
            events.append(
                LogEvent(
                    line_no=i,
                    timestamp=ts,
                    level=level.strip(),
                    trace_id=trace_id.strip(),
                    user=user.strip(),
                    logger=logger.strip(),
                    message=message.strip(),
                )
            )
    return events


def extract_tracebacks(path: Path) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if not path.exists():
        return blocks
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("Traceback (most recent call last):"):
            start = max(0, i - 3)
            block_lines = [lines[i]]
            i += 1
            while i < len(lines) and (
                not lines[i].strip()
                or lines[i][0] in " \t"
                or lines[i].startswith("  File ")
                or lines[i].startswith("Traceback")
            ):
                if lines[i].strip() and not lines[i][0].isspace() and "Traceback" not in lines[i]:
                    break
                block_lines.append(lines[i])
                i += 1
            blocks.append(
                {
                    "start_line": start + 1,
                    "end_line": start + len(block_lines) + 3,
                    "text": "\n".join(lines[start : start + len(block_lines) + 3]),
                }
            )
        else:
            i += 1
    return blocks


def categorize_error(msg: str, logger: str) -> str:
    if "insert_agent_telemetry_batch" in logger or "insert telemetry" in msg.lower():
        return "agent_telemetry_bq"
    if "Side operation exhausted" in msg and "telemetry" in msg.lower():
        return "agent_telemetry_bq"
    if "[Retry]" in msg or "retry" in logger.lower():
        return "agent_retry"
    if "[Validation]" in msg or "validate_final_report" in msg:
        return "validation"
    if "Service error" in msg or "test-project" in msg or "job-" in msg:
        return "test_harness"
    if "Cache creation failed" in msg:
        return "infrastructure"
    if "otel" in logger.lower() or "OTel" in msg or "OpenTelemetry" in msg:
        return "cloud_trace_otel"
    return "other"


def normalize_error_signature(msg: str) -> str:
    s = msg
    for pat in (
        r"job_id=[^\s]+",
        r"session_id=[^\s]+",
        r"job_execution_id=[^\s]+",
        r"\d{4}-\d{2}-\d{2}",
    ):
        s = re.sub(pat, "<ID>", s)
    if len(s) > 200:
        s = s[:200] + "..."
    return s


def is_significant_retry(msg: str) -> bool:
    if "Starting leaf execution" in msg and not any(
        k in msg for k in ("retry", "attempt", "failure", "exhausted")
    ):
        return False
    if "Starting leaf execution" in msg:
        return False
    if "Popped retry hint" in msg or "Stored retry hint" in msg:
        return False
    if "Cleared retry" in msg or "Building continuation" in msg:
        return "Cold retry" in msg or "failure" in msg.lower()
    if "Pipeline] Starting ADK runner" in msg:
        return False
    if "Pipeline] ADK runner finished" in msg:
        return False
    return any(k in msg for k in FAILURE_RETRY_KEYWORDS) or "MISSING_OUTPUT" in msg


def parse_retry_event(ev: LogEvent) -> dict[str, Any]:
    msg = ev.message
    row: dict[str, Any] = {
        "when": ev.timestamp,
        "line": ev.line_no,
        "trace_id": ev.trace_id,
        "level": ev.level,
        "raw": msg,
    }
    m = RETRY_LINE_RE.search(msg)
    row["retry_text"] = m.group(1) if m else msg
    am = AGENT_RE.search(msg)
    if am:
        row["agent"] = am.group(1)
    ecm = ERROR_CLASS_RE.search(msg)
    if ecm:
        row["error_class"] = ecm.group(1)
    atm = ATTEMPT_RE.search(msg)
    if atm:
        row["attempt"] = f"{atm.group(1)}/{atm.group(2)}"
    jm = JOB_ID_RE.search(msg)
    if jm:
        row["job_id"] = jm.group(1)
    sm = SESSION_ID_RE.search(msg)
    if sm:
        row["session_id"] = sm.group(1)
    okm = OUTPUT_KEY_RE.search(msg)
    if okm:
        row["output_key"] = okm.group(1)

    text = row.get("retry_text", msg)
    if "Denied" in text:
        row["action"] = "Denied"
    elif "exhausted" in text.lower():
        row["action"] = "Exhausted"
    elif "Cold retry" in text:
        row["action"] = "Cold retry"
    elif "Approved" in text:
        row["action"] = "Approved"
    elif "Agent output failure" in text:
        row["action"] = "Output failure"
    elif "Preparing retry" in text:
        row["action"] = "Preparing retry"
    elif "Leaf retry" in text:
        row["action"] = "Leaf retry"
    elif "Side operation exhausted" in text:
        row["action"] = "Side op exhausted"
    elif "Fail-fast" in text:
        row["action"] = "Fail-fast"
    elif "Evaluating agent" in text:
        row["action"] = "Evaluating"
    else:
        row["action"] = "Other"
    return row


def analyze(events: list[LogEvent]) -> dict[str, Any]:
    errors = [e for e in events if e.level == "ERROR"]
    warnings = [e for e in events if e.level == "WARNING"]
    critical = [e for e in events if e.level == "CRITICAL"]

    errors_prod = [e for e in errors if e.is_production]
    errors_test = [e for e in errors if e.is_test]

    retry_events = [
        e for e in events if "[Retry]" in e.message and is_significant_retry(e.message)
    ]
    retry_parsed = [parse_retry_event(e) for e in retry_events]
    retry_prod = [r for r in retry_parsed if PROD_TRACE_PREFIX in r.get("trace_id", "")]

    otel_cloud = [
        e
        for e in events
        if "otel_setup" in e.logger
        or "_init_telemetry" in e.message
        or "OpenTelemetry" in e.message
    ]
    otel_init_ok = sum(1 for e in otel_cloud if "initialized successfully" in e.message)
    otel_flush_ok = sum(1 for e in otel_cloud if "force_flush completed" in e.message)
    otel_shutdown_ok = sum(1 for e in otel_cloud if "TracerProvider shut down" in e.message)
    otel_flush_fail = [e for e in otel_cloud if "force_flush failed" in e.message]

    bq_telemetry = [
        e
        for e in events
        if "insert_agent_telemetry_batch" in e.logger
        or "insert_agent_telemetry_batch" in e.message
        or ("Side operation exhausted" in e.message and "telemetry" in e.message.lower())
    ]
    bq_errors = [e for e in bq_telemetry if e.level == "ERROR"]

    cache_failures = [e for e in events if "Cache creation failed" in e.message]

    error_sigs: dict[str, list[LogEvent]] = defaultdict(list)
    for e in errors:
        error_sigs[normalize_error_signature(e.message)].append(e)

    return {
        "counts": {
            "total_parsed_lines": len(events),
            "error": len(errors),
            "error_production": len(errors_prod),
            "error_test": len(errors_test),
            "warning": len(warnings),
            "critical": len(critical),
            "retry_significant": len(retry_events),
            "retry_production": len(retry_prod),
            "cache_creation_failed": len(cache_failures),
            "otel_init_success": otel_init_ok,
            "otel_flush_success": otel_flush_ok,
            "otel_shutdown_success": otel_shutdown_ok,
            "otel_flush_fail": len(otel_flush_fail),
            "bq_telemetry_errors": len(bq_errors),
        },
        "errors": errors,
        "errors_prod": errors_prod,
        "errors_test": errors_test,
        "error_sigs": dict(error_sigs),
        "retry_parsed": retry_parsed,
        "retry_prod": retry_prod,
        "otel_cloud": otel_cloud,
        "otel_flush_fail": otel_flush_fail,
        "bq_telemetry": bq_telemetry,
        "bq_errors": bq_errors,
        "cache_failures": cache_failures,
    }


def md_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_None._\n"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(c).replace("|", "/") for c in row) + " |")
    return "\n".join(lines) + "\n"


def render_report(
    app_path: Path,
    agent_path: Path,
    data: dict[str, Any],
    tracebacks: list[dict[str, Any]],
    agent_log_lines: int,
) -> str:
    c = data["counts"]
    sections: list[str] = []

    sections.append("# Sales-Agent app.log error report\n")
    sections.append(f"**Generated:** {datetime.utcnow().isoformat()}Z\n")
    sections.append(f"**Source:** `{app_path}` ({c['total_parsed_lines']} parsed log lines)\n")
    if agent_path.exists():
        sections.append(f"**Agent log:** `{agent_path}` ({agent_log_lines} lines; PlanReAct replan text only)\n")

    sections.append("## Executive summary\n")
    sections.append(
        "| Finding | Verdict |\n|---------|--------|\n"
        f"| **OpenTelemetry (Cloud Trace)** | Init ×{c['otel_init_success']}, flush ×{c['otel_flush_success']}, shutdown ×{c['otel_shutdown_success']} — "
        + ("**no flush failures**" if c["otel_flush_fail"] == 0 else f"**{c['otel_flush_fail']} flush failure(s)**")
        + " |\n"
        f"| **Agent telemetry (BigQuery)** | **{c['bq_telemetry_errors']} ERROR(s)** — "
        + ("schema/insert failures (see below)" if c["bq_telemetry_errors"] else "none in parsed ERROR lines")
        + " |\n"
        f"| **Production ERROR lines** (`88c3c885...`) | **{c['error_production']}** |\n"
        f"| **Test harness ERROR lines** (`no-trace`) | **{c['error_test']}** |\n"
        f"| **Significant agent retry events** | **{c['retry_significant']}** total, **{c['retry_production']}** on Microsoft trace |\n"
        f"| **Gemini cache DEBUG noise** | {c['cache_creation_failed']} `Cache creation failed` lines |\n"
    )

    sections.append("\n> **Important:** Log messages saying \"telemetry\" may refer to **BigQuery agent metrics** (`insert_agent_telemetry_batch`), not OpenTelemetry Cloud Trace (`otel_setup`).\n")

    sections.append("## Two telemetry systems\n")
    sections.append(md_table(
        ["System", "Log markers", "Status"],
        [
            [
                "OpenTelemetry → Cloud Trace",
                "`src.core.otel_setup`, `_init_telemetry`, `flush_telemetry`, `shutdown_telemetry`",
                f"OK — {c['otel_init_success']} inits, {c['otel_shutdown_success']} clean shutdowns",
            ],
            [
                "Agent telemetry → BigQuery",
                "`insert_agent_telemetry_batch`, `[Retry] Side operation exhausted`",
                f"{'FAILED' if c['bq_telemetry_errors'] else 'No ERROR'} — see BigQuery section",
            ],
        ],
    ))

    sections.append("## Agent retry — production Microsoft run\n")
    sections.append(
        "Trace: `88c3c885fb463ed1b75d31a5c88d2728` | "
        "Job: `job_4cd5af53-ac4e-4948-b195-33e3eb6e9c2a` (from logs)\n\n"
    )
    sections.append("### Why retries happen (code paths)\n")
    sections.append(md_table(
        ["Mechanism", "Source file", "Trigger"],
        [
            [
                "Output validation",
                "`src/services/research/run/resilience/runner_loop.py` → `validate_agent_output`",
                "`MISSING_OUTPUT` if `output_key` empty; `REPORT_VALIDATION_FAILED` for ReportCompiler",
            ],
            [
                "Retry approval",
                "`src/services/research/run/resilience/state.py` → `apply_retry`",
                "Up to `AGENT_RETRY_ATTEMPTS`; denies non-retryable `error_class`",
            ],
            [
                "Cold retry",
                "`pipeline.py` → `run_runner_with_per_agent_retry`",
                "Full pipeline re-invocation (not ADK session resume) for `MISSING_OUTPUT`",
            ],
            [
                "BQ telemetry side retry",
                "`src/services/research/finalization/operations.py` → `with_retry_sync`",
                "Separate from agent retry budget; exhausted at 2 attempts on insert failure",
            ],
        ],
    ))

    prod_rows = []
    for r in data["retry_prod"]:
        prod_rows.append([
            r.get("when", ""),
            r.get("agent", "—"),
            r.get("error_class", "—"),
            r.get("action", ""),
            r.get("attempt", "—"),
            r.get("output_key", "—"),
            str(r.get("line", "")),
        ])
    sections.append("### Retry timeline (production trace only)\n")
    sections.append(md_table(
        ["When", "Agent", "error_class", "Action", "Attempt", "output_key", "Line"],
        prod_rows[:80],
    ))
    if len(prod_rows) > 80:
        sections.append(f"\n_({len(prod_rows) - 80} more rows omitted.)_\n")

    sections.append("### Key production retry incidents\n")
    sections.append(
        "| When | Agent | Why | What |\n|------|-------|-----|------|\n"
        "| 2026-05-31 23:02:38 | AlignmentAnalyst | `MISSING_OUTPUT` — `alignment_output` empty after persistence | Cold retry 1/3; pipeline restarted ~23:02:58 |\n"
        "| 2026-05-31 23:30:42 | ReportCompiler | `MISSING_OUTPUT` — `final_report` empty | Cold retry 1/3; pipeline restarted again |\n"
        "| 2026-05-31 23:53:05 | _(side op)_ | BigQuery `sections_produced` schema | `insert_agent_telemetry_batch` failed; side retries exhausted |\n"
    )

    sections.append("\n## Agent retry — all significant events (sample)\n")
    all_retry_rows = []
    for r in data["retry_parsed"][:100]:
        seg = "prod" if PROD_TRACE_PREFIX in r.get("trace_id", "") else "test"
        all_retry_rows.append([
            seg,
            r.get("when", ""),
            r.get("agent", "—"),
            r.get("error_class", "—"),
            r.get("action", ""),
            str(r.get("line", "")),
        ])
    sections.append(md_table(
        ["Segment", "When", "Agent", "error_class", "Action", "Line"],
        all_retry_rows,
    ))

    sections.append("## BigQuery agent telemetry errors\n")
    bq_rows = []
    for e in data["bq_errors"][:20]:
        sig = normalize_error_signature(e.message)
        if "sections_produced" in e.message:
            root = "`sections_produced` repeated-field / JSON shape mismatch (live table may differ from code schema)"
        else:
            root = sig[:120]
        bq_rows.append([e.timestamp, e.trace_id[:12] + "...", str(e.line_no), root])
    sections.append(md_table(["When", "Trace", "Line", "What"], bq_rows))
    sections.append(
        "\n**Root cause (code review):** "
        "`src/repositories/bigquery_repository.py` serializes `sections_produced` as JSON string; "
        "BigQuery rejected rows with: `Repeated value added outside of an array, field: sections_produced.` "
        "— likely live table schema is REPEATED while code sends JSON/scalar.\n"
    )

    sections.append("## OpenTelemetry (Cloud Trace) events\n")
    otel_rows = []
    for e in data["otel_cloud"]:
        if e.level in ("ERROR", "WARNING") or "success" in e.message.lower() or "shut down" in e.message or "flush" in e.message:
            otel_rows.append([e.timestamp, e.level, e.message[:100], str(e.line_no)])
    sections.append(md_table(["When", "Level", "Message", "Line"], otel_rows[-40:]))

    sections.append("## ERROR summary by category\n")
    cat_counts: Counter[str] = Counter()
    for sig, evs in data["error_sigs"].items():
        if evs:
            cat_counts[categorize_error(evs[0].message, evs[0].logger)] += len(evs)
    sections.append(md_table(
        ["Category", "Count"],
        [[k, str(v)] for k, v in cat_counts.most_common()],
    ))

    sections.append("## ERROR signatures (deduplicated)\n")
    sig_rows = []
    for sig, evs in sorted(data["error_sigs"].items(), key=lambda x: -len(x[1]))[:30]:
        evs_sorted = sorted(evs, key=lambda e: e.timestamp)
        cat = categorize_error(evs[0].message, evs[0].logger)
        seg = "prod" if any(e.is_production for e in evs) else "test"
        sig_rows.append([
            str(len(evs)),
            seg,
            cat,
            evs_sorted[0].timestamp,
            evs_sorted[-1].timestamp,
            sig[:100],
        ])
    sections.append(md_table(
        ["Count", "Segment", "Category", "First", "Last", "Message (normalized)"],
        sig_rows,
    ))

    sections.append("## Production ERROR lines (full)\n")
    prod_err_rows = []
    for e in data["errors_prod"]:
        prod_err_rows.append([e.timestamp, e.logger.split(".")[-1][:40], e.message[:150], str(e.line_no)])
    sections.append(md_table(["When", "Logger", "Message", "Line"], prod_err_rows))

    sections.append("## Test harness ERROR lines (sample)\n")
    test_err_rows = []
    for e in data["errors_test"][:40]:
        test_err_rows.append([e.timestamp, e.message[:120], str(e.line_no)])
    sections.append(md_table(["When", "Message", "Line"], test_err_rows))

    sections.append("## Infrastructure noise\n")
    sections.append(f"- `Cache creation failed` (Gemini context cache): **{c['cache_creation_failed']}** DEBUG lines (non-fatal)\n")

    sections.append("## Tracebacks\n")
    if tracebacks:
        for tb in tracebacks[:10]:
            sections.append(f"### Lines {tb['start_line']}–{tb['end_line']}\n```\n{tb['text'][:2000]}\n```\n")
    else:
        sections.append("_No tracebacks found in app.log._\n")

    sections.append("## Code map (log → source)\n")
    sections.append(md_table(
        ["Log function / message", "File"],
        [
            ["`validate_agent_output`, `run_runner_with_per_agent_retry`", "`src/services/research/run/resilience/runner_loop.py`"],
            ["`apply_retry`, `prepare_agent_retry`", "`src/services/research/run/resilience/state.py`"],
            ["`insert_agent_telemetry_batch`", "`src/repositories/bigquery_repository.py`"],
            ["`with_retry` side operations", "`src/services/research/finalization/operations.py`"],
            ["`setup_telemetry`, `flush_telemetry`, `shutdown_telemetry`", "`src/core/otel_setup.py`"],
            ["`track_agent_end`, telemetry records", "`src/services/research/run/telemetry.py`"],
        ],
    ))

    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze Sales-Agent logs")
    parser.add_argument("--app-log", default="out/latest/app.log")
    parser.add_argument("--agent-log", default="out/latest/agent.log")
    parser.add_argument("--out", default="out/latest/app-error-report.md")
    parser.add_argument("--json", default="out/latest/app-error-report.json")
    args = parser.parse_args()

    app_path = Path(args.app_log)
    agent_path = Path(args.agent_log)
    out_path = Path(args.out)
    json_path = Path(args.json)

    events = parse_log_file(app_path)
    tracebacks = extract_tracebacks(app_path)
    agent_log_lines = len(agent_path.read_text(encoding="utf-8", errors="replace").splitlines()) if agent_path.exists() else 0

    data = analyze(events)
    report = render_report(app_path, agent_path, data, tracebacks, agent_log_lines)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    json_payload = {
        "counts": data["counts"],
        "retry_production": data["retry_prod"],
        "production_errors": [
            {
                "timestamp": e.timestamp,
                "line": e.line_no,
                "logger": e.logger,
                "message": e.message,
            }
            for e in data["errors_prod"]
        ],
    }
    json_path.write_text(json.dumps(json_payload, indent=2), encoding="utf-8")

    print(f"Wrote {out_path} ({len(report)} chars)")
    print(f"Wrote {json_path}")
    print("Counts:", json.dumps(data["counts"], indent=2))


if __name__ == "__main__":
    main()
