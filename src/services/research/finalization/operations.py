"""Composable side-operation helpers for finalization."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from ....core.config import settings
from ....core.logging_config import logger
from ..run.telemetry import TELEMETRY_RECORDS_KEY
from ..utils.async_retry import with_retry, with_retry_sync
from ..utils.metrics import reconcile_cost


async def run_pdf_op(
    *,
    job_id: str,
    final_report: str,
    generate_pdf: Callable[[str], bytes],
    upload_pdf: Callable[[str, bytes], Any],
) -> bool:
    pdf_available = False

    async def _op():
        nonlocal pdf_available
        pdf_bytes = await asyncio.to_thread(generate_pdf, final_report)
        await asyncio.to_thread(upload_pdf, job_id, pdf_bytes)
        pdf_available = True

    await with_retry(_op)
    return pdf_available


async def run_evaluation_op(
    *,
    job_id: str,
    final_report: str,
    session_state: dict,
    update_status: Callable[..., Any],
    evaluate: Callable[[str, str, dict], Any],
    upload_evaluation: Callable[[str, Any], Any],
) -> None:
    async def _op():
        update_status(
            job_id,
            "PROCESSING",
            progress=settings.RESEARCH_EVAL_PROGRESS,
            current_step=settings.RESEARCH_EVAL_STEP_LABEL,
        )
        result = await evaluate(job_id, final_report, session_state)
        upload_evaluation(job_id, result)

    await with_retry(_op)


async def run_cost_attribution_op(
    *,
    job_id: str,
    session_state: dict,
    metrics: dict,
    metadata: dict | None = None,
    insert_cost_attribution: Callable[..., Any],
) -> None:
    reconcile_cost(session_state, metrics)
    metadata = metadata or {}
    await with_retry_sync(
        lambda: insert_cost_attribution(
            job_id=job_id,
            username=metadata.get("username"),
            email=metadata.get("user_id"),
            business_unit=metadata.get("business_unit"),
            model_version=settings.GEMINI_MODEL,
            temperature=metrics["temperature"],
            prompt_template_version=settings.PROMPT_TEMPLATE_VERSION,
            input_tokens=metrics["input_tokens"] or None,
            output_tokens=metrics["output_tokens"] or None,
            total_tokens=metrics["total_tokens"] or None,
            latency_seconds=metrics["latency"],
            source_domains=metrics["source_domains"] or None,
            cost_usd=metrics["cost_usd"],
        )
    )


async def run_telemetry_flush_op(
    *,
    job_id: str,
    session_state: dict,
    insert_agent_telemetry_batch: Callable[[list[dict]], Any],
    upload_deadletter_json: Callable[[str, dict], Any],
) -> None:
    telemetry_records = session_state.get(TELEMETRY_RECORDS_KEY) or []
    if not telemetry_records:
        return
    try:
        await with_retry_sync(lambda: insert_agent_telemetry_batch(telemetry_records))
    except Exception as e:
        logger.warning(f"[Retry] Telemetry flush failed job_id={job_id}: {e}")
        with contextlib.suppress(Exception):
            upload_deadletter_json(
                f"{job_id}_telemetry_deadletter",
                {"records": telemetry_records, "error": str(e)},
            )
        raise
