"""Tests for the layered per-domain output persistence."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.worker.agents.tools.domain_outputs import (
    MIN_DOMAIN_CHARS,
    canonical_domain_key,
    extract_domain_payloads,
    missing_domain_keys,
    recover_domain_outputs,
    save_domain_output,
    write_domain_output,
)
from src.worker.domain.contracts import (
    DOMAIN_OUTPUT_KEYS,
    validate_domain_outputs_present,
)

PAYLOAD = "x" * (MIN_DOMAIN_CHARS + 10)


def _tool_context(state: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(state=state if state is not None else {})


# --- key normalization ----------------------------------------------------


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("firmographicsagent_output", "firmographicsagent_output"),
        ("firmographics", "firmographicsagent_output"),
        ("Firmographics_Output", "firmographicsagent_output"),
        ("growth_signals", "growthsignals_output"),
        ("growthsignals", "growthsignals_output"),
        ("tech-stack agent output", None),
        ("", None),
        ("nonsense_output", None),
    ],
)
def test_canonical_domain_key(supplied: str, expected: str | None) -> None:
    assert canonical_domain_key(supplied) == expected


# --- tool path ------------------------------------------------------------


def test_save_domain_output_stores_and_reports_progress() -> None:
    ctx = _tool_context()
    result = save_domain_output("firmographics", PAYLOAD, ctx)

    assert result["status"] == "SAVED"
    assert result["domain_key"] == "firmographicsagent_output"
    assert ctx.state["firmographicsagent_output"] == PAYLOAD
    assert result["saved_count"] == 1
    assert "firmographicsagent_output" not in result["remaining"]


def test_save_domain_output_rejects_unknown_key_and_stubs() -> None:
    ctx = _tool_context()

    assert save_domain_output("not_a_domain", PAYLOAD, ctx)["status"] == "ERROR"
    assert save_domain_output("marketagent_output", "N/A", ctx)["status"] == "ERROR"
    assert ctx.state == {}


def test_save_domain_output_overwrites_with_a_correction() -> None:
    ctx = _tool_context()
    save_domain_output("marketagent_output", PAYLOAD, ctx)
    save_domain_output("marketagent_output", "corrected " + PAYLOAD, ctx)

    assert ctx.state["marketagent_output"].startswith("corrected ")


def test_saving_every_domain_satisfies_the_gate() -> None:
    ctx = _tool_context()
    for key in DOMAIN_OUTPUT_KEYS:
        save_domain_output(key, PAYLOAD, ctx)

    assert missing_domain_keys(ctx.state) == []
    validate_domain_outputs_present(ctx.state)


# --- text recovery --------------------------------------------------------


def test_extract_domain_payloads_from_clean_json() -> None:
    blob = json.dumps({key: {"value": key} for key in DOMAIN_OUTPUT_KEYS})

    found = extract_domain_payloads(blob)

    assert set(found) == set(DOMAIN_OUTPUT_KEYS)
    assert json.loads(found["marketagent_output"]) == {"value": "marketagent_output"}


def test_extract_domain_payloads_survives_truncation() -> None:
    blob = json.dumps({key: {"value": key} for key in DOMAIN_OUTPUT_KEYS})
    truncated = blob[: int(len(blob) * 0.6)]  # cut mid-object, no closing brace

    found = extract_domain_payloads(truncated)

    assert 0 < len(found) < len(DOMAIN_OUTPUT_KEYS)
    assert "firmographicsagent_output" in found


def test_extract_domain_payloads_reads_fenced_block() -> None:
    blob = (
        "/*FINAL_ANSWER*/\n```json\n"
        + json.dumps({"executiveagent_output": {"ceo": "A. Person"}})
        + "\n```"
    )

    assert "executiveagent_output" in extract_domain_payloads(blob)


def test_extract_ignores_nested_lookalike_fields() -> None:
    blob = json.dumps(
        {"firmographicsagent_output": {"market": "beauty retail", "sector": "retail"}}
    )

    found = extract_domain_payloads(blob)

    assert set(found) == {"firmographicsagent_output"}


def test_recover_does_not_clobber_tool_saved_domains() -> None:
    state = {"marketagent_output": PAYLOAD}
    blob = json.dumps({"marketagent_output": "tiny"})

    assert recover_domain_outputs(state, blob, source="test") == []
    assert state["marketagent_output"] == PAYLOAD


def test_recover_upgrades_a_shorter_existing_payload() -> None:
    state = {"marketagent_output": "short"}
    blob = json.dumps({"marketagent_output": PAYLOAD})

    assert recover_domain_outputs(state, blob, source="test") == ["marketagent_output"]
    assert state["marketagent_output"] == PAYLOAD


def test_write_domain_output_ignores_blank_and_unknown() -> None:
    state: dict = {}

    assert not write_domain_output(state, "marketagent_output", "   ", source="test")
    assert not write_domain_output(state, "bogus", PAYLOAD, source="test")
    assert state == {}
