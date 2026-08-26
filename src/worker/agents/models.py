"""Typed inputs/outputs passed explicitly between pipeline steps.

No shared mutable session state: QueryPlanner returns a QueryPlan,
SearchExecutor consumes it and returns SearchFindings, AlignmentAnalyst
consumes SearchFindings and returns ColtAlignment, and ReportCompiler
consumes exactly SearchFindings + ColtAlignment (via CompilerInput) --
matching the data flow specified by the user, nothing more.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class ResearchRequest:
    """Entry point for the whole pipeline."""

    job_id: str
    company: str


@dataclass(frozen=True, slots=True)
class Query:
    text: str
    domain: str


@dataclass(frozen=True, slots=True)
class QueryPlan:
    """Output of QueryPlanner: the BM25-selected queries to search."""

    company: str
    queries: tuple[Query, ...]


@dataclass(frozen=True, slots=True)
class Evidence:
    url: str = ""
    title: str = ""
    snippet: str = ""
    query: str = ""
    authoritative: bool = False
    flagged_injection: bool = False


@dataclass(frozen=True, slots=True)
class DomainFinding:
    """Everything found for one of the 12 canonical research domains."""

    domain: str
    content: str
    evidence: tuple[Evidence, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Result of one executed (or attempted) search query."""

    query: Query
    succeeded: bool
    text: str = ""
    evidence: tuple[Evidence, ...] = ()
    error_kind: str | None = None

    @classmethod
    def ok(cls, query: Query, text: str, evidence: tuple[Evidence, ...]) -> QueryResult:
        return cls(query=query, succeeded=True, text=text, evidence=evidence)

    @classmethod
    def failed(cls, query: Query, error_kind: str) -> QueryResult:
        return cls(query=query, succeeded=False, error_kind=error_kind)


@dataclass(frozen=True, slots=True)
class SearchFindings:
    """Output of SearchExecutor: the 12 canonical domains, populated from
    only the queries that actually succeeded. Never contains fabricated
    placeholder text for a failed query (see IMPLEMENTATION_PLAN.md R3/R4).
    """

    company: str
    domains: Mapping[str, DomainFinding]
    executed: int
    failed: tuple[str, ...] = ()

    @property
    def total_queries(self) -> int:
        return self.executed + len(self.failed)

    @property
    def success_rate(self) -> float:
        total = self.total_queries
        if total == 0:
            return 1.0
        return self.executed / total

    @property
    def populated_domain_count(self) -> int:
        return sum(1 for f in self.domains.values() if f.content.strip())

    def all_evidence(self) -> tuple[Evidence, ...]:
        result: list[Evidence] = []
        for finding in self.domains.values():
            result.extend(finding.evidence)
        return tuple(result)


@dataclass(frozen=True, slots=True)
class ColtAlignmentMapping:
    challenge: str
    solution: str
    justification: str


@dataclass(frozen=True, slots=True)
class ColtAlignment:
    """Output of AlignmentAnalyst."""

    mappings: tuple[ColtAlignmentMapping, ...]
    opportunity_summary: str
    hooks: tuple[str, ...] = ()
    executive_narratives: tuple[str, ...] = ()
    regulatory_triggers: tuple[str, ...] = ()
    ai_urgency: tuple[str, ...] = ()
    competitive_displacement_angles: tuple[str, ...] = ()
    colt_differentiation: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CompilerInput:
    """Explicit, minimal input to ReportCompiler: only what the user asked
    for -- search findings and the Colt alignment -- never raw session
    context or the outputs of the query planner.
    """

    company: str
    findings: SearchFindings
    alignment: ColtAlignment


@dataclass(frozen=True, slots=True)
class Report:
    markdown: str
    validation_status: str = "UNKNOWN"
    validation_violations: tuple[dict[str, str], ...] = ()


@dataclass(slots=True)
class PipelineResult:
    """Final pipeline output plus the compatibility bridge that lets
    finalization_service / evaluation / artifacts keep working unchanged.
    """

    report: Report
    findings: SearchFindings
    alignment: ColtAlignment
    telemetry_records: list[dict[str, Any]] = field(default_factory=list)
    token_usage_by_model: dict[str, dict[str, int]] = field(default_factory=dict)
    search_query_records: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None

    def to_legacy_state(self) -> dict[str, Any]:
        """Emit the exact keys finalization_service/evaluation/artifacts/
        metrics currently read from ADK session_state (verified list in
        IMPLEMENTATION_PLAN.md section 10.1).
        """
        job_evidence = [
            {
                "url": e.url,
                "title": e.title,
                "snippet": e.snippet,
                "query": e.query,
                "authoritative": e.authoritative,
                "flagged_injection": e.flagged_injection,
            }
            for e in self.findings.all_evidence()
        ]
        input_tokens = sum(
            v.get("input", 0) for v in self.token_usage_by_model.values()
        )
        output_tokens = sum(
            v.get("output", 0) for v in self.token_usage_by_model.values()
        )
        return {
            "company_name": self.findings.company,
            "final_report": self.report.markdown,
            "job_evidence": job_evidence,
            "raw_search_cache": job_evidence,
            "agent_telemetry_records": self.telemetry_records,
            "mc_input_tokens": input_tokens,
            "mc_output_tokens": output_tokens,
            "mc_tokens_by_model": self.token_usage_by_model,
            "mc_temperature": self.temperature,
            "mc_search_count": self.findings.executed,
            "search_count": self.findings.executed,
            "report_validation_status": self.report.validation_status,
            "report_validation_violations": list(self.report.validation_violations),
            "search_query_records": self.search_query_records,
        }


__all__ = [
    "ResearchRequest",
    "Query",
    "QueryPlan",
    "Evidence",
    "DomainFinding",
    "QueryResult",
    "SearchFindings",
    "ColtAlignmentMapping",
    "ColtAlignment",
    "CompilerInput",
    "Report",
    "PipelineResult",
]
