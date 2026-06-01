"""Application-layer command objects for research pipeline execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResearchJobCommand:
    """Input command for executing a background research job."""

    job_id: str
    company_name: str
    metadata: dict[str, Any] = field(default_factory=dict)
