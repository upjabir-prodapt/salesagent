"""Core domain models for research job orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResearchJob:
    """Research job identity and caller context."""

    job_id: str
    company_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchMetrics:
    """Normalized model-card and runtime metrics."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_seconds: float = 0.0
    cost_usd: float | None = None
    temperature: float | None = None


@dataclass
class EvidenceRecord:
    """Normalized evidence item captured by tools/callbacks."""

    url: str = ""
    title: str = ""
    snippet: str = ""
    query: str = ""
    agent: str = ""
    authoritative: bool | None = None
    flagged_injection: bool = False
