"""Agent telemetry tracking and per-agent metrics."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from google.adk.agents.callback_context import CallbackContext

from src.shared.config import settings

from .pricing import (
    calculate_delta_cost,
    snapshot_tokens_by_model,
    total_tokens_from_by_model,
)

_AGENT_TYPE_MAP: dict[str, str] = {
    "QueryGeneratorAgent": "LlmAgent",
    "ParallelSearchAgent": "DeterministicAgent",
    "AlignmentAnalyst": "LlmAgent",
    "ReportCompiler": "LlmAgent",
}

_PFX_START = "at_start_"
_PFX_SNAP_IN = "at_snap_in_"
_PFX_SNAP_OUT = "at_snap_out_"
_PFX_SNAP_TOKENS_BY_MODEL = "at_snap_tokens_by_model_"

TELEMETRY_RECORDS_KEY = "agent_telemetry_records"


@dataclass
class AgentTelemetryRecord:
    """Per-agent telemetry row matching the BigQuery agent_telemetry schema."""

    record_id: str
    job_execution_id: str
    agent_name: str
    agent_type: str | None
    latency_ms: int | None
    tokens_input: int | None
    tokens_output: int | None
    model_used: str | None = None
    cost_usd: float | None = None
    success: bool = True
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id,
            "job_execution_id": self.job_execution_id,
            "agent_name": self.agent_name,
            "agent_type": self.agent_type,
            "latency_ms": self.latency_ms,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "model_used": self.model_used,
            "cost_usd": self.cost_usd,
            "success": self.success,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
        }


def track_agent_start(callback_context: CallbackContext) -> None:
    agent_name = callback_context.agent_name
    state = callback_context.state
    state[f"{_PFX_START}{agent_name}"] = time.monotonic()
    state[f"{_PFX_SNAP_IN}{agent_name}"] = state.get("mc_input_tokens") or 0
    state[f"{_PFX_SNAP_OUT}{agent_name}"] = state.get("mc_output_tokens") or 0
    state[f"{_PFX_SNAP_TOKENS_BY_MODEL}{agent_name}"] = snapshot_tokens_by_model(state)


def track_agent_end(
    callback_context: CallbackContext,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    agent_name = callback_context.agent_name
    state = callback_context.state
    job_id = state.get("job_execution_id") or "unknown"

    start = state.get(f"{_PFX_START}{agent_name}")
    latency_ms: int | None = None
    if start is not None:
        latency_ms = int((time.monotonic() - start) * 1000)

    snap_in = state.get(f"{_PFX_SNAP_IN}{agent_name}") or 0
    snap_out = state.get(f"{_PFX_SNAP_OUT}{agent_name}") or 0
    snap_tokens_by_model = state.get(f"{_PFX_SNAP_TOKENS_BY_MODEL}{agent_name}") or {}
    current_tokens_by_model = snapshot_tokens_by_model(state)
    current_in = state.get("mc_input_tokens") or 0
    current_out = state.get("mc_output_tokens") or 0
    delta_in = max(0, current_in - snap_in)
    delta_out = max(0, current_out - snap_out)
    if current_tokens_by_model:
        delta_in, delta_out = total_tokens_from_by_model(
            {
                model: {
                    "input": max(
                        0,
                        int(current_tokens_by_model.get(model, {}).get("input") or 0)
                        - int(snap_tokens_by_model.get(model, {}).get("input") or 0),
                    ),
                    "output": max(
                        0,
                        int(current_tokens_by_model.get(model, {}).get("output") or 0)
                        - int(snap_tokens_by_model.get(model, {}).get("output") or 0),
                    ),
                }
                for model in set(snap_tokens_by_model) | set(current_tokens_by_model)
            }
        )

    cost_usd: float | None = None
    if current_tokens_by_model or snap_tokens_by_model:
        cost_usd = calculate_delta_cost(snap_tokens_by_model, current_tokens_by_model)

    model_used = settings.GEMINI_MODEL

    record = AgentTelemetryRecord(
        record_id=str(uuid.uuid4()),
        job_execution_id=job_id,
        agent_name=agent_name,
        agent_type=_AGENT_TYPE_MAP.get(agent_name),
        latency_ms=latency_ms,
        tokens_input=delta_in or None,
        tokens_output=delta_out or None,
        model_used=model_used,
        cost_usd=cost_usd,
        success=success,
        error_message=error_message,
        created_at=datetime.now(UTC),
    )

    records: list[dict] = list(state.get(TELEMETRY_RECORDS_KEY) or [])
    records.append(record.to_dict())
    state[TELEMETRY_RECORDS_KEY] = records


__all__ = [
    "TELEMETRY_RECORDS_KEY",
    "AgentTelemetryRecord",
    "track_agent_start",
    "track_agent_end",
]
