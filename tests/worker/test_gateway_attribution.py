"""Attribution, isolation and the pricing firewall.

These three properties are the ones that fail *silently* if they regress:
usage attributed to the wrong user, cost recorded as zero, or a report that
passes validation with no citations. None of them raises on its own.
"""

from __future__ import annotations

import asyncio

import pytest

from src.shared import llm_gateway as gw
from src.shared.config import settings
from src.shared.llm_identity import use_llm_identity


@pytest.fixture
def gateway_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True, raising=False)
    monkeypatch.setattr(
        settings,
        "LLM_GATEWAY_BASE_URL",
        "https://llm.aicoedev-int.colt.net",
        raising=False,
    )
    monkeypatch.setattr(
        settings, "LLM_GATEWAY_API_KEY_SECRET", "TESTKEY", raising=False
    )
    return settings


@pytest.mark.asyncio
async def test_concurrent_jobs_do_not_share_identity(gateway_on):
    """The reason a ContextVar was chosen over a module-level value.

    Two jobs run concurrently in one worker process. If identity were stored
    anywhere shared -- or read once at client construction -- the second job
    would overwrite the first and both would be attributed to the same user,
    with nothing to indicate it.
    """
    captured: dict[str, dict[str, str]] = {}

    async def run_job(job: str, oid: str, department: str) -> None:
        with use_llm_identity(oid, department):
            await asyncio.sleep(0)  # force interleaving
            captured[job] = gw.gateway_identity_headers()

    await asyncio.gather(
        run_job("a", "OID-a", "Alpha"),
        run_job("b", "OID-b", "Beta"),
        run_job("c", "OID-c", "Gamma"),
    )

    assert captured["a"][gw.HEADER_USER_OID] == "OID-a"
    assert captured["b"][gw.HEADER_USER_OID] == "OID-b"
    assert captured["c"][gw.HEADER_USER_OID] == "OID-c"
    assert captured["a"][gw.HEADER_USER_DEPARTMENT] == "Alpha"


@pytest.mark.asyncio
async def test_identity_survives_a_thread_offload(gateway_on):
    """Several call sites run under `asyncio.to_thread`, which copies context."""
    with use_llm_identity("OID-threaded", "Ops"):
        headers = await asyncio.to_thread(gw.gateway_identity_headers)
    assert headers[gw.HEADER_USER_OID] == "OID-threaded"


def test_usage_reporting_strips_the_gateway_prefix(gateway_on):
    """Cost would silently become zero without this.

    `AdkAgentStep.execute` reports `agent.model.model` as the model name. Under
    the gateway that is `apigee/vertex_ai/<model>`, and the pricing registry's
    `normalize_model_name` strips only a `models/` prefix -- so the lookup
    misses, no exception is raised, and the recorded cost is zero.
    """
    from src.worker.runtime.pricing import normalize_model_name

    prefixed = gw.apigee_model_string("gemini-3.5-flash")
    # The failure this guards against: the raw prefixed name does not normalise
    # to anything the registry knows.
    assert normalize_model_name(prefixed) != "gemini-3.5-flash"
    # ...and the firewall restores it.
    assert (
        normalize_model_name(gw.strip_gateway_model_prefix(prefixed))
        == "gemini-3.5-flash"
    )


def test_search_validate_rejects_a_grounded_run_with_no_citations(gateway_on):
    """A stripped google_search tool yields prose with zero sources.

    `success_rate` measures text arriving, not citations, so without this guard
    the pipeline would emit a passing, uncited report and nothing would flag it.
    """
    from src.worker.agents.base import InvalidOutputError
    from src.worker.agents.models import SearchFindings
    from src.worker.agents.search import SearchExecutor

    executor = SearchExecutor.__new__(SearchExecutor)
    object.__setattr__(executor, "_min_success_rate", 0.0)

    findings = SearchFindings(company="Acme", domains={}, executed=5, failed=())
    assert findings.all_evidence() == ()

    with pytest.raises(InvalidOutputError, match="zero grounding citations"):
        executor.validate(findings)


def test_search_validate_allows_zero_citations_when_gateway_is_off(monkeypatch):
    """Local direct-to-Vertex runs have no proxy to blame; do not fail them."""
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False, raising=False)

    from src.worker.agents.models import SearchFindings
    from src.worker.agents.search import SearchExecutor

    executor = SearchExecutor.__new__(SearchExecutor)
    object.__setattr__(executor, "_min_success_rate", 0.0)
    executor.validate(SearchFindings(company="Acme", domains={}, executed=5, failed=()))
