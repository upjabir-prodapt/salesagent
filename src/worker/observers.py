"""Observers for pipeline execution: telemetry, progress, and tracing.

Replaces src/worker/agents/callbacks/ (804 lines of before/after ADK
callbacks reading and writing shared session state) with explicit method
calls made directly by Agent.run() and ResearchPipeline.run(). Each
concrete Observer is independent and composed via CompositeObserver, so
adding a new cross-cutting concern (e.g. metrics) never touches agent code.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from src.shared.logging_config import logger

if TYPE_CHECKING:
    from .agents.base import ErrorKind

_AGENT_TYPE_MAP: dict[str, str] = {
    "QueryPlanner": "LlmAgent",
    "SearchExecutor": "DeterministicAgent",
    "AlignmentAnalyst": "LlmAgent",
    "ReportCompiler": "LlmAgent",
}


class Observer(ABC):
    """Lifecycle hooks called by Agent.run() around every attempt."""

    @abstractmethod
    def on_start(self, agent_name: str, attempt: int) -> None: ...

    @abstractmethod
    def on_retry(
        self, agent_name: str, attempt: int, kind: ErrorKind, delay: float
    ) -> None: ...

    @abstractmethod
    def on_success(self, agent_name: str, attempt: int, seconds: float) -> None: ...

    @abstractmethod
    def on_failure(
        self, agent_name: str, attempt: int, kind: ErrorKind, exc: BaseException
    ) -> None: ...

    def on_usage(
        self, agent_name: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        """Optional: called when a step captures token usage."""
        return


class CompositeObserver(Observer):
    """Fan out every hook call to a list of child observers."""

    def __init__(self, observers: list[Observer]) -> None:
        self._observers = observers

    def on_start(self, agent_name: str, attempt: int) -> None:
        for obs in self._observers:
            obs.on_start(agent_name, attempt)

    def on_retry(
        self, agent_name: str, attempt: int, kind: ErrorKind, delay: float
    ) -> None:
        for obs in self._observers:
            obs.on_retry(agent_name, attempt, kind, delay)

    def on_success(self, agent_name: str, attempt: int, seconds: float) -> None:
        for obs in self._observers:
            obs.on_success(agent_name, attempt, seconds)

    def on_failure(
        self, agent_name: str, attempt: int, kind: ErrorKind, exc: BaseException
    ) -> None:
        for obs in self._observers:
            obs.on_failure(agent_name, attempt, kind, exc)

    def on_usage(
        self, agent_name: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        for obs in self._observers:
            obs.on_usage(agent_name, model, input_tokens, output_tokens)


@dataclass
class _AgentRunRecord:
    agent_name: str
    started_at: float = field(default_factory=time.monotonic)
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    model_used: str | None = None


class TelemetryObserver(Observer):
    """Builds per-agent telemetry rows matching the BigQuery agent_telemetry
    schema. Replaces runtime/telemetry.py's track_agent_start/track_agent_end
    pair, which used to diff token snapshots stored in shared session state.
    """

    def __init__(self, job_id: str) -> None:
        self._job_id = job_id
        self._records: dict[str, _AgentRunRecord] = {}
        self._finished: list[dict[str, Any]] = []

    def _record(self, agent_name: str) -> _AgentRunRecord:
        rec = self._records.get(agent_name)
        if rec is None:
            rec = _AgentRunRecord(agent_name=agent_name)
            self._records[agent_name] = rec
        return rec

    def on_start(self, agent_name: str, attempt: int) -> None:
        rec = self._record(agent_name)
        if attempt == 1:
            rec.started_at = time.monotonic()
        rec.attempts = attempt

    def on_retry(
        self, agent_name: str, attempt: int, kind: ErrorKind, delay: float
    ) -> None:
        return

    def on_success(self, agent_name: str, attempt: int, seconds: float) -> None:
        rec = self._record(agent_name)
        self._finished.append(self._to_row(rec, success=True, error_message=None))

    def on_failure(
        self, agent_name: str, attempt: int, kind: ErrorKind, exc: BaseException
    ) -> None:
        rec = self._record(agent_name)
        self._finished.append(self._to_row(rec, success=False, error_message=str(exc)))

    def on_usage(
        self, agent_name: str, model: str, input_tokens: int, output_tokens: int
    ) -> None:
        rec = self._record(agent_name)
        rec.input_tokens += input_tokens
        rec.output_tokens += output_tokens
        rec.model_used = model

    def _to_row(
        self, rec: _AgentRunRecord, *, success: bool, error_message: str | None
    ) -> dict[str, Any]:
        latency_ms = int((time.monotonic() - rec.started_at) * 1000)
        return {
            "record_id": str(uuid.uuid4()),
            "job_execution_id": self._job_id,
            "agent_name": rec.agent_name,
            "agent_type": _AGENT_TYPE_MAP.get(rec.agent_name),
            "latency_ms": latency_ms,
            "tokens_input": rec.input_tokens or None,
            "tokens_output": rec.output_tokens or None,
            "model_used": rec.model_used,
            "cost_usd": None,
            "success": success,
            "error_message": error_message,
            "created_at": datetime.now(UTC).isoformat(),
        }

    def records(self) -> list[dict[str, Any]]:
        return list(self._finished)

    def token_usage(self) -> dict[str, dict[str, int]]:
        """mc_tokens_by_model-shaped summary for the legacy state bridge."""
        by_model: dict[str, dict[str, int]] = {}
        for rec in self._records.values():
            if not rec.model_used:
                continue
            bucket = by_model.setdefault(rec.model_used, {"input": 0, "output": 0})
            bucket["input"] += rec.input_tokens
            bucket["output"] += rec.output_tokens
        return by_model


class ProgressObserver(Observer):
    """Writes coarse job progress/current_step to BigQuery as each agent
    starts and finishes. Replaces runtime/progress.py's event-stream-based
    ResearchProgressTracker, which parsed ADK events off a shared runner.
    """

    def __init__(
        self,
        job_id: str,
        update_status: Any,
        total_agents: int,
        *,
        min_interval_seconds: float = 5.0,
    ) -> None:
        self._job_id = job_id
        self._update_status = update_status
        self._total_agents = max(total_agents, 1)
        self._completed = 0
        self._min_interval = min_interval_seconds
        self._last_write = 0.0

    def _write(self, *, current_step: str, progress: int | None = None) -> None:
        now = time.monotonic()
        if progress is None and (now - self._last_write) < self._min_interval:
            return
        self._last_write = now
        try:
            self._update_status(
                self._job_id,
                None,
                progress=progress,
                current_step=current_step,
            )
        except Exception as exc:  # pragma: no cover - best-effort telemetry
            logger.debug(f"[Progress] status write failed: {exc}")

    def on_start(self, agent_name: str, attempt: int) -> None:
        label = (
            f"Running: {agent_name}"
            if attempt == 1
            else f"Retrying {agent_name} (attempt {attempt})"
        )
        self._write(current_step=label)

    def on_retry(
        self, agent_name: str, attempt: int, kind: ErrorKind, delay: float
    ) -> None:
        return

    def on_success(self, agent_name: str, attempt: int, seconds: float) -> None:
        self._completed += 1
        pct = min(int((self._completed / self._total_agents) * 100), 99)
        self._write(current_step=f"{agent_name} completed", progress=pct)

    def on_failure(
        self, agent_name: str, attempt: int, kind: ErrorKind, exc: BaseException
    ) -> None:
        self._write(current_step=f"{agent_name} failed: {kind}")


class TracingObserver(Observer):
    """Wraps each step attempt in an OpenTelemetry span."""

    def __init__(self) -> None:
        self._tracer = trace.get_tracer(__name__)
        self._spans: dict[tuple[str, int], Any] = {}
        self._cms: dict[tuple[str, int], Any] = {}

    def on_start(self, agent_name: str, attempt: int) -> None:
        cm = self._tracer.start_as_current_span(f"agent.{agent_name}")
        span = cm.__enter__()
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("agent.attempt", attempt)
        self._cms[(agent_name, attempt)] = cm
        self._spans[(agent_name, attempt)] = span

    def on_retry(
        self, agent_name: str, attempt: int, kind: ErrorKind, delay: float
    ) -> None:
        span = self._spans.get((agent_name, attempt))
        if span is not None:
            span.add_event("retry", {"kind": str(kind), "delay_seconds": delay})
        self._exit(agent_name, attempt)

    def on_success(self, agent_name: str, attempt: int, seconds: float) -> None:
        span = self._spans.get((agent_name, attempt))
        if span is not None:
            span.set_attribute("agent.duration_seconds", seconds)
        self._exit(agent_name, attempt)

    def on_failure(
        self, agent_name: str, attempt: int, kind: ErrorKind, exc: BaseException
    ) -> None:
        span = self._spans.get((agent_name, attempt))
        if span is not None:
            span.set_attribute("agent.error_kind", str(kind))
            span.record_exception(exc)
        self._exit(agent_name, attempt)

    def _exit(self, agent_name: str, attempt: int) -> None:
        cm = self._cms.pop((agent_name, attempt), None)
        self._spans.pop((agent_name, attempt), None)
        if cm is not None:
            cm.__exit__(None, None, None)


__all__ = [
    "Observer",
    "CompositeObserver",
    "TelemetryObserver",
    "ProgressObserver",
    "TracingObserver",
]
