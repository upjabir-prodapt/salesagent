"""
Agent Telemetry Module

Tracks per-agent latency, token usage, cost, and sections produced.
Integrates with the ADK callback system by recording snapshots into session state
on agent entry (track_agent_start) and computing deltas on agent exit (track_agent_end).

Records are accumulated in session state under "agent_telemetry_records" and flushed
to BigQuery by the research service after the full agent run completes.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from google.adk.agents.callback_context import CallbackContext

from ....core.logging_config import logger
from ..utils.model_pricing import (
    calculate_delta_cost,
    resolve_model_used_for_delta,
    snapshot_tokens_by_model,
    total_tokens_from_by_model,
)

# ---------------------------------------------------------------------------
# Agent classification maps
# ---------------------------------------------------------------------------

# LLM agents to track - leaf research agents + synthesis agents that make
# substantive LLM calls and contribute to token cost.
_AGENT_TYPE_MAP: dict[str, str] = {
    # Research leaf agents
    "FirmographicsAgent": "LlmAgent",
    "GeographicAgent": "LlmAgent",
    "StrategyAgent": "LlmAgent",
    "ComplianceAgent": "LlmAgent",
    "MarketAgent": "LlmAgent",
    "EcosystemAgent": "LlmAgent",
    "TechStackAgent": "LlmAgent",
    "ProcurementAgent": "LlmAgent",
    "GrowthSignals": "LlmAgent",
    "RiskSignals": "LlmAgent",
    "CampaignSignals": "LlmAgent",
    # Synthesis agents - included for cost attribution coverage
    "AlignmentAnalyst": "LlmAgent",
    "ReportCompiler": "LlmAgent",
}

# Maps each tracked agent -> the report section(s) it produces.
_AGENT_SECTIONS_MAP: dict[str, list[str]] = {
    "FirmographicsAgent": ["firmographics"],
    "GeographicAgent": ["geographic"],
    "StrategyAgent": ["strategy"],
    "ComplianceAgent": ["compliance"],
    "MarketAgent": ["market"],
    "EcosystemAgent": ["ecosystem"],
    "TechStackAgent": ["tech_stack"],
    "ProcurementAgent": ["procurement"],
    "GrowthSignals": ["growth_signals"],
    "RiskSignals": ["risk_signals"],
    "CampaignSignals": ["campaign_signals"],
    "AlignmentAnalyst": ["alignment_mappings", "strategic_opportunity"],
    "ReportCompiler": ["final_report"],
}

# Session-state key prefix constants (keep consistent across start/end)
_PFX_START = "at_start_"
_PFX_SNAP_IN = "at_snap_in_"
_PFX_SNAP_OUT = "at_snap_out_"
_PFX_SNAP_TOKENS_BY_MODEL = "at_snap_tokens_by_model_"
_PFX_SNAP_TOOLS = "at_snap_tools_"  # web tool call count snapshot

# Key under which completed records are accumulated in session state
TELEMETRY_RECORDS_KEY = "agent_telemetry_records"

__all__ = ["TELEMETRY_RECORDS_KEY", "track_agent_end", "track_agent_start"]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


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
    sections_produced: list[str] = field(default_factory=list)
    sources_crawled: int | None = None
    model_used: str | None = None
    cost_usd: float | None = None
    success: bool = True
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict:
        """Serialize to a plain dict suitable for BigQuery streaming insert."""
        return {
            "record_id": self.record_id,
            "job_execution_id": self.job_execution_id,
            "agent_name": self.agent_name,
            "agent_type": self.agent_type,
            "latency_ms": self.latency_ms,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "sections_produced": self.sections_produced,
            "sources_crawled": self.sources_crawled,
            "model_used": self.model_used,
            "cost_usd": self.cost_usd,
            "success": self.success,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Tracking helpers (called from ADK callbacks)
# ---------------------------------------------------------------------------


def track_agent_start(callback_context: CallbackContext) -> None:
    """
    Record a per-agent start snapshot into session state.
    Call this from before_agent_callback.

    Stores:
      - monotonic start timestamp
      - snapshot of accumulated global token counts
      - snapshot of unique domain count
    """
    agent_name = callback_context.agent_name
    if agent_name not in _AGENT_TYPE_MAP:
        return

    state = callback_context.state
    state[f"{_PFX_START}{agent_name}"] = time.monotonic()
    state[f"{_PFX_SNAP_IN}{agent_name}"] = state.get("mc_input_tokens") or 0
    state[f"{_PFX_SNAP_OUT}{agent_name}"] = state.get("mc_output_tokens") or 0
    state[f"{_PFX_SNAP_TOKENS_BY_MODEL}{agent_name}"] = snapshot_tokens_by_model(state)
    state[f"{_PFX_SNAP_TOOLS}{agent_name}"] = state.get("mc_tool_call_count") or 0


def track_agent_end(
    callback_context: CallbackContext,
    success: bool = True,
    error_message: str | None = None,
) -> None:
    """
    Compute per-agent deltas, build an AgentTelemetryRecord, and append it to
    session state under TELEMETRY_RECORDS_KEY.

    Call this from after_agent_callback.  The research service flushes all
    accumulated records to BigQuery after the full run completes.
    """
    agent_name = callback_context.agent_name
    if agent_name not in _AGENT_TYPE_MAP:
        return

    state = callback_context.state
    job_id = state.get("job_execution_id") or "unknown"

    # --- latency ---
    start = state.get(f"{_PFX_START}{agent_name}")
    latency_ms: int | None = None
    if start is not None:
        latency_ms = int((time.monotonic() - start) * 1000)

    # --- token deltas (per-agent tokens = current global minus snapshot at entry) ---
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

    # --- sources crawled (google_search + read_url calls made by this agent) ---
    snap_tools = state.get(f"{_PFX_SNAP_TOOLS}{agent_name}") or 0
    current_tools = state.get("mc_tool_call_count") or 0
    sources_crawled = max(0, current_tools - snap_tools)

    # --- cost ---
    cost_usd: float | None = None
    if current_tokens_by_model or snap_tokens_by_model:
        cost_usd = calculate_delta_cost(snap_tokens_by_model, current_tokens_by_model)

    model_used = resolve_model_used_for_delta(
        snap_tokens_by_model, current_tokens_by_model, agent_name
    )

    record = AgentTelemetryRecord(
        record_id=str(uuid.uuid4()),
        job_execution_id=job_id,
        agent_name=agent_name,
        agent_type=_AGENT_TYPE_MAP.get(agent_name),
        latency_ms=latency_ms,
        tokens_input=delta_in or None,
        tokens_output=delta_out or None,
        sections_produced=_AGENT_SECTIONS_MAP.get(agent_name, []),
        sources_crawled=sources_crawled or None,
        model_used=model_used,
        cost_usd=cost_usd,
        success=success,
        error_message=error_message,
        created_at=datetime.now(UTC),
    )

    # Accumulate into session state list for bulk flush by the research service
    records: list[dict] = list(state.get(TELEMETRY_RECORDS_KEY) or [])
    records.append(record.to_dict())
    state[TELEMETRY_RECORDS_KEY] = records

    logger.debug(
        f"[Telemetry] {agent_name}: latency={latency_ms}ms "
        f"tokens_in={delta_in} tokens_out={delta_out} "
        f"sources={sources_crawled} cost={cost_usd}"
    )
