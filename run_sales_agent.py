#!/usr/bin/env python3
"""
Run the Sales Research Agent locally (ADK).

Examples:
  uv run python run_sales_agent.py --smoke
  uv run python run_sales_agent.py --quick --company "Colt Technology Services"
  uv run python run_sales_agent.py --company "Colt Technology Services"
  PYTHONUNBUFFERED=1 uv run python run_sales_agent.py --company "Microsoft" --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.run_config import RunConfig
from google.adk.apps import App
from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from src.agents.salesAgent.agent import create_sales_agent_app
from src.agents.salesAgent.utils import create_plan_react_agent
from src.agents.salesAgent.prompts import FIRMOGRAPHICS_PROMPT
from src.core.logging_config import logger, setup_logging
from src.utils.agent import log_event

USER_ID = "local_sales_user"
DEFAULT_COMPANY = "Colt Technology Services"


def _collect_agents(agent: Any, depth: int = 0) -> list[tuple[int, str, str]]:
    """Return (depth, name, description) for the agent tree."""
    rows = [(depth, agent.name, getattr(agent, "description", "") or "")]
    for sub in getattr(agent, "sub_agents", None) or []:
        rows.extend(_collect_agents(sub, depth + 1))
    return rows


def _count_llm_leaves(agent: Any) -> int:
    if isinstance(agent, LlmAgent):
        return 1
    return sum(_count_llm_leaves(s) for s in getattr(agent, "sub_agents", None) or [])


def smoke_test() -> None:
    """Build app and runner without calling the LLM."""
    print("=== Sales Agent smoke test ===\n")
    app = create_sales_agent_app()
    root = app.root_agent
    rows = _collect_agents(root)
    for depth, name, desc in rows:
        indent = "  " * depth
        print(f"{indent}- {name}: {desc[:80]}{'...' if len(desc) > 80 else ''}")
    leaves = _count_llm_leaves(root)
    print(f"\nLlmAgent leaves: {leaves}")
    assert leaves >= 14, f"expected >=14 leaves, got {leaves}"

    runner = Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )

    async def _session() -> None:
        session = await runner.session_service.create_session(
            app_name=app.name,
            user_id=USER_ID,
            session_id=f"smoke_{uuid.uuid4().hex[:8]}",
            state={"company_name": DEFAULT_COMPANY},
        )
        print(f"Session created: {session.id}")
        print(f"Initial state keys: {sorted(session.state.keys())}")

    asyncio.run(_session())
    print("\nSmoke test PASSED")


def _log(msg: str, *, err: bool = False) -> None:
    print(msg, file=sys.stderr if err else sys.stdout, flush=True)


def _print_event(event_num: int, event: Any) -> None:
    author = getattr(event, "author", "?")
    content = getattr(event, "content", None)
    if not content or not content.parts:
        return
    for part in content.parts:
        if fc := getattr(part, "function_call", None):
            name = getattr(fc, "name", None)
            if name:
                _log(f"  [{event_num}] {author} ACTION: {name}")
            continue
        if fr := getattr(part, "function_response", None):
            name = getattr(fr, "name", None)
            if name:
                _log(f"  [{event_num}] {author} TOOL: {name}")
            continue
        text = (part.text or "").strip()
        if text and not getattr(part, "thought", False):
            preview = text[:120].replace("\n", " ")
            _log(f"  [{event_num}] {author}: {preview}{'...' if len(text) > 120 else ''}")


async def run_quick_research(company_name: str, *, verbose: bool = True) -> tuple[str, dict]:
    """Run a single Firmographics PlanReAct agent (faster E2E check)."""
    agent = create_plan_react_agent(
        "FirmographicsAgent",
        FIRMOGRAPHICS_PROMPT,
        description="Quick-run firmographics only.",
    )
    app = App(
        name="sales_quick_app",
        root_agent=SequentialAgent(
            name="QuickResearch",
            sub_agents=[agent],
            description="Single-agent quick run",
        ),
    )
    runner = Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id=USER_ID,
        session_id=f"quick_{uuid.uuid4().hex[:8]}",
        state={"company_name": company_name},
    )
    message = types.UserContent(
        parts=[types.Part(text=f"Research firmographics for {company_name}")]
    )
    _log(f"\n=== Quick run (FirmographicsAgent only): {company_name} ===\n")
    event_num = 0
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=message,
        run_config=RunConfig(),
    ):
        event_num += 1
        log_event(event, verbose=verbose)
    session = await runner.session_service.get_session(
        app_name=app.name, user_id=USER_ID, session_id=session.id
    )
    state = dict(session.state) if session else {}
    output = state.get("firmographicsagent_output", "")
    return output, state


async def run_sales_research(company_name: str, *, verbose: bool = False) -> tuple[str, dict]:
    """Run the full SalesResearchAgent pipeline for one company."""
    app = create_sales_agent_app()
    runner = Runner(
        app=app,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )
    session_id = f"sales_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(
        app_name=app.name,
        user_id=USER_ID,
        session_id=session_id,
        state={"company_name": company_name},
    )

    message = types.UserContent(
        parts=[
            types.Part(
                text=f"Run the Sales Research Agent for the company {company_name}"
            )
        ]
    )

    _log(f"\n=== Sales Research run: {company_name} ===")
    _log(f"Session: {session.id}\n")

    event_num = 0
    async for event in runner.run_async(
        user_id=USER_ID,
        session_id=session.id,
        new_message=message,
        run_config=RunConfig(),
    ):
        event_num += 1
        log_event(event, verbose=verbose)
        if event_num % 100 == 0:
            logger.info(
                "Progress: %s events (latest author=%s)",
                event_num,
                getattr(event, "author", "?"),
            )

    session = await runner.session_service.get_session(
        app_name=app.name, user_id=USER_ID, session_id=session.id
    )
    state = dict(session.state) if session else {}
    final_report = state.get("final_report", "")
    return final_report, state


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sales Research Agent")
    parser.add_argument(
        "--company",
        default=DEFAULT_COMPANY,
        help="Company name to research",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Build app and session only (no LLM calls)",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run one Firmographics PlanReAct agent only (faster E2E)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print more agent events during the run",
    )
    args = parser.parse_args()

    # Uses LOG_LEVEL / DEBUG from .env (see src/core/logging_config.py)
    setup_logging()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.smoke:
        smoke_test()
        return

    try:
        if args.quick:
            output, state = asyncio.run(
                run_quick_research(args.company, verbose=args.verbose or True)
            )
            _log("\n=== Quick run complete ===\n")
            _log(f"firmographicsagent_output length: {len(str(output))}")
            _log(f"verification_status: {state.get('verification_status')}")
            if not output:
                _log("WARNING: firmographicsagent_output empty", err=True)
                sys.exit(1)
            _log("\nVerification: OK (quick agent produced output)")
            return
        final_report, state = asyncio.run(
            run_sales_research(args.company, verbose=args.verbose)
        )
    except Exception as exc:
        print(f"\nRun FAILED: {exc}", file=sys.stderr)
        raise

    print("\n=== Run complete ===\n")
    output_keys = sorted(k for k in state if k.endswith("_output") or k == "final_report")
    print(f"State keys (outputs): {output_keys}")
    print(f"final_report length: {len(final_report)} chars")

    if not final_report:
        print("WARNING: final_report is empty", file=sys.stderr)
        sys.exit(1)

    print("\n--- final_report (first 2000 chars) ---\n")
    print(final_report[:2000])
    if len(final_report) > 2000:
        print(f"\n... [{len(final_report) - 2000} more chars]")

    print("\nVerification: OK (final_report present)")


if __name__ == "__main__":
    main()
