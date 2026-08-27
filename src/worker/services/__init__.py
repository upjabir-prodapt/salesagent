"""Worker services package.

Note: ResearchJobRunner is deliberately NOT imported here at package level.
It imports src.worker.pipeline, which imports src.worker.agents.compiler,
which imports src.worker.services.formatting -- importing any submodule of
`services` first executes this __init__.py, so eagerly importing
job_runner here would create a circular import
(services -> job_runner -> pipeline -> agents.compiler -> services).
Import it directly: `from src.worker.services.job_runner import ResearchJobRunner`.
"""

from .artifacts import ResearchArtifactService
from .async_retry import with_retry, with_retry_sync
from .finalization_ops import (
    run_cost_attribution_op,
    run_evaluation_op,
    run_pdf_op,
    run_search_log_op,
    run_telemetry_flush_op,
)
from .finalization_service import ResearchFinalizationService
from .formatting import clean_markdown_report
from .metrics import calculate_metrics, reconcile_cost
from .status import (
    build_completion_metadata,
    build_failure_summary,
    build_model_card,
)

__all__ = [
    "ResearchArtifactService",
    "ResearchFinalizationService",
    "run_pdf_op",
    "run_evaluation_op",
    "run_cost_attribution_op",
    "run_telemetry_flush_op",
    "run_search_log_op",
    "clean_markdown_report",
    "calculate_metrics",
    "reconcile_cost",
    "build_completion_metadata",
    "build_failure_summary",
    "build_model_card",
    "with_retry",
    "with_retry_sync",
]
