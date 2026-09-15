"""Contract tests for the Apigee LLM gateway wiring.

The centrepiece is `test_apigee_model_builds_the_allow_listed_url`. The gateway
enforces its model allow-list by matching the exact request path, and Apigee
does that matching BEFORE any policy in the proxy runs -- so a wrong path is
rejected with a credential-shaped error that looks nothing like a URL problem.
Asserting the full URL string catches that here instead.

It is also the tripwire for a google-genai or ADK upgrade: the correct URL
depends on three things holding together (project/location both None, a
base_url ending /v1, and ResourceScope.COLLECTION), and an SDK change to any of
them silently re-breaks the path.
"""

from __future__ import annotations

import pytest

from src.shared import llm_gateway as gw
from src.shared.config import settings
from src.shared.llm_identity import current_llm_identity, use_llm_identity

GATEWAY_HOST = "https://llm.aicoedev-int.colt.net"
MODEL_PATH = "publishers/google/models/gemini-3.5-flash:generateContent"
EXPECTED_URL = f"{GATEWAY_HOST}/v1/{MODEL_PATH}"


@pytest.fixture
def gateway_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "LLM_GATEWAY_BASE_URL", GATEWAY_HOST, raising=False)
    monkeypatch.setattr(
        settings, "LLM_GATEWAY_API_KEY_SECRET", "TESTKEY", raising=False
    )
    return settings


@pytest.fixture
def gateway_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "LLM_GATEWAY_ENABLED", False, raising=False)
    return settings


def test_apigee_model_builds_the_allow_listed_url(gateway_on):
    from src.worker.model import ApigeeRegionalLlm, build_llm

    model = build_llm("gemini-3.5-flash")
    assert isinstance(model, ApigeeRegionalLlm)
    assert model.model == "apigee/vertex_ai/gemini-3.5-flash"

    api_client = model.api_client._api_client
    # Both must be None: either one set re-prefixes the path with this
    # workload's project and the infra region, and re-enables ADC token minting.
    assert api_client.project is None
    assert api_client.location is None

    request = api_client._build_request("post", MODEL_PATH, {}, None)
    headers = {k.lower(): v for k, v in (request.headers or {}).items()}
    assert request.url == EXPECTED_URL
    assert headers["x-apikey"] == "TESTKEY"
    # A custom base_url makes google-genai skip load_auth(), so x-apikey is the
    # only credential leaving this service -- which is what VA-ApiKey expects.
    assert "authorization" not in headers


def test_disabled_gateway_uses_the_direct_vertex_model(gateway_off):
    from src.worker.model import RegionalGemini, build_llm

    model = build_llm("gemini-3.5-flash")
    assert isinstance(model, RegionalGemini)
    assert model.model == "gemini-3.5-flash"


def test_model_prefix_never_reaches_pricing(gateway_on):
    """The prefix belongs at the ADK boundary and nowhere else.

    `normalize_model_name` in the pricing registry strips only a `models/`
    prefix, so an `apigee/...` value misses every lookup and the cost silently
    becomes zero -- no exception anywhere. This round-trip is the firewall.
    """
    prefixed = gw.apigee_model_string("gemini-3.5-flash")
    assert prefixed == "apigee/vertex_ai/gemini-3.5-flash"
    assert gw.strip_gateway_model_prefix(prefixed) == "gemini-3.5-flash"
    # Bare names pass through untouched, so stripping is always safe to apply.
    assert gw.strip_gateway_model_prefix("gemini-3.5-flash") == "gemini-3.5-flash"
    # Idempotent: never apigee/vertex_ai/apigee/vertex_ai/...
    assert gw.apigee_model_string(prefixed) == prefixed


def test_prefix_is_not_applied_when_gateway_disabled(gateway_off):
    assert gw.apigee_model_string("gemini-3.5-flash") == "gemini-3.5-flash"


def test_identity_is_per_request_not_baked_into_the_client(gateway_on):
    """The anti-regression for the whole design.

    `api_client` is a cached_property and the raw genai client is a singleton,
    so identity baked in at construction would pin the first job's user onto
    every later call for the container's lifetime. Identity must therefore be
    absent from the client's own headers.
    """
    from src.worker.model import build_llm

    with use_llm_identity("OID-123", "Network Engineering"):
        model = build_llm("gemini-3.5-flash")
        client_headers = model.api_client._api_client._http_options.headers or {}

    lowered = {k.lower() for k in client_headers}
    assert gw.HEADER_USER_OID not in lowered
    assert gw.HEADER_USER_DEPARTMENT not in lowered
    assert "x-apikey" in lowered


def test_request_http_options_carry_identity(gateway_on):
    with use_llm_identity("OID-123", "Network Engineering"):
        options = gw.gateway_request_http_options()
    assert options is not None
    assert options.headers[gw.HEADER_USER_OID] == "OID-123"
    assert options.headers[gw.HEADER_USER_DEPARTMENT] == "Network Engineering"


def test_company_header_is_never_sent(gateway_on):
    """Company is always Colt, so it carries no analytical value (docs/23 S4.2)."""
    with use_llm_identity("OID-123", "Network Engineering"):
        headers = gw.gateway_identity_headers()
    assert "x-colt-user-company" not in {k.lower() for k in headers}


def test_identity_headers_omitted_when_no_user(gateway_on):
    """Omit rather than invent a sentinel; Apigee applies system/unattributed."""
    assert current_llm_identity() is None
    assert gw.gateway_identity_headers() == {}
    assert gw.gateway_request_http_options() is None


def test_identity_does_not_leak_between_scopes(gateway_on):
    """Cloud Run reuses containers, so a job must not inherit the previous user."""
    with use_llm_identity("OID-first", "Alpha"):
        assert gw.gateway_identity_headers()[gw.HEADER_USER_OID] == "OID-first"
    assert gw.gateway_identity_headers() == {}
    with use_llm_identity("OID-second", "Beta"):
        assert gw.gateway_identity_headers()[gw.HEADER_USER_OID] == "OID-second"


@pytest.mark.parametrize(
    "configured",
    [GATEWAY_HOST, f"{GATEWAY_HOST}/", f"{GATEWAY_HOST}/v1", f"{GATEWAY_HOST}/v1/"],
)
def test_base_url_normalisation_is_idempotent(gateway_on, monkeypatch, configured):
    monkeypatch.setattr(settings, "LLM_GATEWAY_BASE_URL", configured, raising=False)
    assert gw.gateway_base_url() == f"{GATEWAY_HOST}/v1"


def test_project_and_location_forced_to_none(gateway_on):
    assert gw.gateway_vertex_identity_kwargs("gclt-aicoe-dev-st", "europe-west1") == {
        "project": None,
        "location": None,
    }


def test_disabled_gateway_is_a_full_passthrough(gateway_off):
    assert gw.gateway_enabled() is False
    assert gw.gateway_client_kwargs() == {}
    assert gw.gateway_vertex_identity_kwargs("proj", "europe-west1") == {
        "project": "proj",
        "location": "europe-west1",
    }


def test_missing_api_key_fails_loudly(gateway_on, monkeypatch):
    """Previously the key was silently omitted and every call 401'd with no local signal."""
    monkeypatch.setattr(settings, "LLM_GATEWAY_API_KEY_SECRET", "", raising=False)
    with pytest.raises(ValueError, match="LLM_GATEWAY_API_KEY_SECRET"):
        gw.gateway_client_kwargs()


def test_allow_list_matches_the_models_this_service_uses(gateway_on):
    """Apigee rejects a non-allow-listed model before any policy runs."""
    for model in (settings.GEMINI_MODEL, settings.SEARCH_AGENT_MODEL):
        assert model in gw.ALLOWED_MODELS, (
            f"{model!r} is configured but not on the gateway allow-list; "
            "Apigee would reject every call using it"
        )
