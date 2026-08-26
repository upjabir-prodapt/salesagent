"""Typed accessors for mutable research session state."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from .agent_contracts import AGENT_OUTPUT_KEYS, get_output_key


class ResearchSessionState:
    """Small typed facade over ADK session state dict-like objects."""

    def __init__(self, state: MutableMapping[str, Any]) -> None:
        self._state = state

    @property
    def raw(self) -> MutableMapping[str, Any]:
        return self._state

    @property
    def final_report(self) -> str:
        return str(self._state.get("final_report") or "")

    @final_report.setter
    def final_report(self, value: str) -> None:
        self._state["final_report"] = value

    @property
    def job_evidence(self) -> list[dict[str, Any]]:
        value = self._state.get("job_evidence")
        return value if isinstance(value, list) else []

    @job_evidence.setter
    def job_evidence(self, value: list[dict[str, Any]]) -> None:
        self._state["job_evidence"] = value

    @property
    def telemetry_records(self) -> list[dict[str, Any]]:
        value = self._state.get("agent_telemetry_records")
        return value if isinstance(value, list) else []

    @telemetry_records.setter
    def telemetry_records(self, value: list[dict[str, Any]]) -> None:
        self._state["agent_telemetry_records"] = value

    @property
    def verification_status(self) -> str | None:
        value = self._state.get("report_validation_status")
        return str(value) if value is not None else None

    @verification_status.setter
    def verification_status(self, value: str) -> None:
        self._state["report_validation_status"] = value

    def get_agent_output(self, agent_name: str) -> str:
        key = get_output_key(agent_name)
        if key is None:
            return ""
        value = self._state.get(key)
        return str(value) if value is not None else ""

    def set_agent_output(self, agent_name: str, value: str) -> None:
        key = get_output_key(agent_name)
        if key:
            self._state[key] = value

    def tracked_outputs(self) -> dict[str, str]:
        outputs: dict[str, str] = {}
        for agent_name, output_key in AGENT_OUTPUT_KEYS.items():
            value = self._state.get(output_key)
            if value is None:
                continue
            text_value = str(value).strip()
            if text_value:
                outputs[agent_name] = text_value
        return outputs
