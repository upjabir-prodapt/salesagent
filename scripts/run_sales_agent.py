#!/usr/bin/env python3
"""
Run the Sales Research Agent locally (ADK only — no FastAPI).

Full runs use ResearchRunnerService (same path as the API). Quick/smoke modes are minimal.

Examples:
  uv run python scripts/run_sales_agent.py --smoke
  uv run python scripts/run_sales_agent.py --quick --company "Colt Technology Services"
  uv run python scripts/run_sales_agent.py --company "Colt Technology Services"
  uv run python scripts/run_sales_agent.py --company "Microsoft" --verbose --out-dir out
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from src.core.config import settings  # noqa: F401 — loads .env
from src.core.exceptions import AgentOutputError, ServiceError
from src.core.logging_config import setup_logging
from src.repositories.bigquery_repository import BigQueryRepository
from src.services.research.agent.sales.agent import create_sales_agent_app
from src.services.research.agent.sales.prompts import FIRMOGRAPHICS_PROMPT
from src.services.research.agent.sales.utils import create_plan_react_agent
from src.services.research.agent.session_ids import runner_session_id
from src.services.research.agent.utils.agent import log_event
from src.services.research.runner_service import ResearchRunnerService

ADK_USER_ID = "api_user"
DEFAULT_COMPANY = "Colt Technology Services"


def _job_id(tag: str | None = None) -> str:
    if tag:
        return f"{settings.JOB_ID_PREFIX}{tag}_{uuid.uuid4().hex[:12]}"
    return f"{settings.JOB_ID_PREFIX}{uuid.uuid4()}"


def _runner() -> ResearchRunnerService:
    return ResearchRunnerService(BigQueryRepository(client=None))


def _save_outputs(save_dir: Path, state: dict, final_report: str) -> None:
    save_dir.mkdir(parents=True, exist_ok=True)
    (save_dir / "final_report.md").write_text(final_report, encoding="utf-8")
    for key, value in sorted(state.items()):
        if not (key.endswith("_output") or key == "final_report"):
            continue
        name = key.removesuffix("_output") if key != "final_report" else "final_report"
        path = save_dir / f"{name}.json"
        if isinstance(value, (dict, list)):
            path.write_text(json.dumps(value, indent=2), encoding="utf-8")
        else:
            path.write_text(str(value), encoding="utf-8")
    (save_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "report_validation_status": state.get("report_validation_status"),
                "report_validation_violations": state.get("report_validation_violations"),
                "output_keys": sorted(
                    k for k in state if k.endswith("_output") or k == "final_report"
                ),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved outputs to {save_dir.resolve()}", flush=True)


def _count_leaves(agent: object) -> int:
    if isinstance(agent, LlmAgent):
        return 1
    return sum(_count_leaves(s) for s in getattr(agent, "sub_agents", None) or [])


def _collect_agents(agent: object, depth: int = 0) -> list[tuple[int, str]]:
    rows = [(depth, agent.name)]
    for sub in getattr(agent, "sub_agents", None) or []:
        rows.extend(_collect_agents(sub, depth + 1))
    return rows


def smoke_test() -> None:
    print("=== Sales Agent smoke test ===\n")
    app = create_sales_agent_app()
    for depth, name in _collect_agents(app.root_agent):
        print(f"{'  ' * depth}- {name}")
    leaves = _count_leaves(app.root_agent)
    print(f"\nLlmAgent leaves: {leaves}")
    assert leaves >= 14

    job_id = _job_id("smoke")
    runner = Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )

    async def _go() -> None:
        session = await runner.session_service.create_session(
            app_name=app.name,
            user_id=ADK_USER_ID,
            session_id=runner_session_id(job_id),
            state={"company_name": DEFAULT_COMPANY, "job_execution_id": job_id},
        )
        print(f"Job id: {job_id}\nSession: {session.id}")

    asyncio.run(_go())
    print("\nSmoke test PASSED")


async def run_quick(company: str) -> tuple[str, dict]:
    agent = create_plan_react_agent(
        "FirmographicsAgent",
        FIRMOGRAPHICS_PROMPT,
        description="Quick-run firmographics only.",
    )
    app = App(
        name="sales_quick_app",
        root_agent=SequentialAgent(
            name="QuickResearch", sub_agents=[agent], description="Quick run"
        ),
    )
    job_id = _job_id("quick")
    runner = Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id=ADK_USER_ID,
        session_id=runner_session_id(job_id),
        state={"company_name": company, "job_execution_id": job_id},
    )
    async for event in runner.run_async(
        user_id=ADK_USER_ID,
        session_id=session.id,
        new_message=types.UserContent(
            parts=[types.Part(text=f"Research firmographics for {company}")]
        ),
        run_config=RunConfig(),
    ):
        log_event(
            event,
            verbose=settings.AGENT_EVENT_LOG_VERBOSE,
            log_file=settings.agent_event_log_path,
        )
    fresh = await runner.session_service.get_session(
        app_name=app.name, user_id=ADK_USER_ID, session_id=session.id
    )
    state = dict(fresh.state) if fresh else {}
    return state.get("firmographicsagent_output", ""), state


async def run_full(company: str) -> tuple[str, dict, str]:
    job_id = _job_id("local")
    try:
        final_report, state = await _runner().run(job_id, company)
    except ServiceError as exc:
        raise AgentOutputError(
            str(exc), agent_name="SalesResearchAgent", output_key="final_report"
        ) from exc
    return final_report, state, job_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sales Research Agent locally")
    parser.add_argument("--company", default=DEFAULT_COMPANY)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=_REPO_ROOT / "out")
    args = parser.parse_args()

    setup_logging()
    if args.verbose:
        import logging

        logging.getLogger().setLevel(logging.DEBUG)

    if args.smoke:
        smoke_test()
        return

    try:
        if args.quick:
            output, state = asyncio.run(run_quick(args.company))
            print("\n=== Quick run complete ===\n", flush=True)
            print(f"firmographicsagent_output length: {len(str(output))}")
            if not output:
                sys.exit(1)
            return

        final_report, state, job_id = asyncio.run(run_full(args.company))
    except AgentOutputError as exc:
        print(f"\nPipeline FAILED ({exc.agent_name}): {exc}", file=sys.stderr)
        sys.exit(1)

    if not state.get("alignment_output"):
        print("ERROR: alignment_output is empty", file=sys.stderr)
        sys.exit(1)

    print("\n=== Run complete ===\n", flush=True)
    print(f"Job id: {job_id}")
    print(f"final_report length: {len(final_report)} chars")
    if args.out_dir:
        _save_outputs(args.out_dir, state, final_report)
    if not final_report:
        sys.exit(1)
    print(final_report[:2000])


if __name__ == "__main__":
    main()
