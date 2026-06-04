from __future__ import annotations

from types import SimpleNamespace

from src.services.research.utils.status import (
    build_completion_metadata,
    build_failure_summary,
    build_model_card,
)


def test_build_completion_metadata_includes_reconciliation(mock_settings) -> None:
    reconciliation = {"delta_cost_usd": 0.0}
    meta = build_completion_metadata(
        latency=3.5,
        metrics={"total_tokens": 100, "cost_usd": 0.42},
        pdf_available=True,
        side_op_failures={},
        reconciliation=reconciliation,
    )

    assert meta["model_version"] == mock_settings.GEMINI_MODEL
    assert meta["cost_reconciliation"] == reconciliation
    assert meta["pdf_available"] is True


def test_build_failure_summary_counts_dominant_rule() -> None:
    violations = [
        SimpleNamespace(rule="citation", detail="missing source"),
        SimpleNamespace(rule="citation", detail="broken link"),
        SimpleNamespace(rule="length", detail="too short"),
    ]

    summary = build_failure_summary(violations)

    assert summary["dominant_rule"] == "citation"
    assert len(summary["all_violations"]) == 3


def test_build_failure_summary_empty_violations() -> None:
    summary = build_failure_summary([])

    assert summary["dominant_rule"] == "unknown"
    assert summary["all_violations"] == []


def test_build_model_card_from_metadata() -> None:
    card = build_model_card(
        {
            "model_version": "gemini-2.5-pro",
            "tokens_used": 500,
            "latency_seconds": 1.2,
            "cost_usd": 0.05,
        }
    )

    assert card["model_version"] == "gemini-2.5-pro"
    assert card["tokens_used"] == 500
