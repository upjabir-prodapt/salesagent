"""Routing every LLM call through the Apigee `llm` gateway.

No workload service account in `gclt-aicoe-dev-st` may hold
`roles/aiplatform.user` (LLD decision D-30), so the gateway is not the preferred
path to Vertex AI -- it is the only working one. A direct call returns 403
regardless of what the code does.

The URL contract -- read this before changing anything here
-----------------------------------------------------------
`base_url` on its own DOES NOT WORK, and fails in a way that looks like
something else entirely. `google-genai` joins `base_url` with the request path
only when `not custom_base_url or (project and location) or api_key`. With a
custom base_url and no project/location/api-key all three are false, so
`url = base_url` and **the entire path is discarded** -- the SDK POSTs to the
bare host. Apigee sees `proxy.pathsuffix == "/"`, no RouteRule matches, and its
`no-match` rule returns 404. Nothing about that 404 hints at a client-side URL
problem.

`base_url_resource_scope=ResourceScope.COLLECTION` takes the other branch and
joins the **unversioned** path. All three of these are required together:

    project=None, location=None          (gateway_vertex_identity_kwargs)
    base_url ending in /v1               (gateway_base_url)
    base_url_resource_scope=COLLECTION   (gateway_client_kwargs)

Remove any one and the request is silently mis-addressed. Verified against
google-genai 1.75.0 by building the request:

    https://llm.aicoedev-int.colt.net/v1/publishers/google/models/<model>:generateContent

`api_version` is irrelevant under COLLECTION -- no version segment is appended,
the `/v1` comes from our own base_url, matching the proxy's BasePath. And
because a custom base_url makes the SDK skip `load_auth()`, no ADC bearer token
is minted: `x-apikey` is the only credential on the wire, which is what the
proxy's `VA-ApiKey` policy expects.

Connection config vs identity -- keep these apart
-------------------------------------------------
`gateway_client_kwargs()` is the **connection** shape and is read once, at
client construction. `gateway_identity_headers()` is **per request**. That split
is load-bearing: this service's genai client is a process-wide singleton and
`ApigeeLlm.api_client` is a `cached_property`, so folding identity into the
connection would freeze the first job's user onto every later call for the
container's lifetime -- mis-attributing everything, silently. Credential on the
client, identity on the request.
"""

from __future__ import annotations

from .config import settings
from .llm_identity import current_llm_identity

# Canonical D2 header names (AICOE-Terraform docs/20 S0a, docs/23 S4.2). Do not
# invent new ones. `x-colt-user-company` is deliberately absent: it is always
# Colt, so it carries no analytical value.
HEADER_USER_OID = "x-colt-user-oid"
HEADER_USER_DEPARTMENT = "x-colt-user-department"

# ADK routes `apigee/<provider>/...` model strings; `vertex_ai` is required (not
# `gemini`) so the request keeps the Vertex shape -- a bare
# `publishers/google/models/...` path, which is what the gateway's allow-list
# matches on. Two components only: a third would pin an `api_version` that
# COLLECTION scope makes irrelevant anyway.
APIGEE_MODEL_PREFIX = "apigee/vertex_ai/"

# Mirrors the aicoe-llm product's llmOperationGroup. Apigee rejects anything
# else during credential/operation matching -- before any policy in the proxy
# runs -- so a typo here surfaces as an opaque credential error deep inside a
# call. Keep in step with apigee/products/products.json in AICOE-Terraform.
ALLOWED_MODELS = frozenset({"gemini-3.5-flash", "gemini-2.5-flash", "gemini-2.5-pro"})


def gateway_enabled() -> bool:
    """True when LLM traffic should be routed through Apigee."""
    return bool(settings.LLM_GATEWAY_ENABLED and settings.LLM_GATEWAY_BASE_URL)


def gateway_base_url() -> str:
    """The gateway base URL, normalised to exactly one trailing `/v1`.

    The proxy's BasePath is `/v1`, and under COLLECTION scope the SDK appends no
    version segment, so the `/v1` must come from here. Normalising rather than
    requiring it means a payload that omits it is not a silent 404, and one that
    includes it does not become `/v1/v1`. Idempotent by design.
    """
    base = str(settings.LLM_GATEWAY_BASE_URL or "").rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/v1") else f"{base}/v1"


def _require_api_key() -> str:
    """The Apigee developer-app key, or a clear failure.

    An earlier version omitted `x-apikey` entirely when this was blank, so the
    gateway was called *unauthenticated* and rejected every request with no
    local signal. Failing here names the real cause. Despite the `_SECRET`
    suffix this holds the raw consumer key, not a Secret Manager resource name.
    """
    key = str(settings.LLM_GATEWAY_API_KEY_SECRET or "").strip()
    if not key:
        raise ValueError(
            "LLM_GATEWAY_ENABLED is true but LLM_GATEWAY_API_KEY_SECRET is empty. "
            "The gateway would reject every call with 401. Set the Apigee "
            "developer-app key in this service's /secrets/.env payload, or set "
            "LLM_GATEWAY_ENABLED=false."
        )
    return key


def apigee_model_string(model: str) -> str:
    """The ADK model string for `model`, prefixed only when the gateway is on.

    The prefix belongs at the ADK model boundary and NOWHERE else. The same bare
    names feed the pricing registry and cost/telemetry recording, where an
    `apigee/...` value silently misses every lookup and zeroes the cost.
    """
    if not gateway_enabled() or model.startswith("apigee/"):
        return model
    return f"{APIGEE_MODEL_PREFIX}{model}"


def strip_gateway_model_prefix(model: str) -> str:
    """Undo `apigee_model_string` for pricing and telemetry lookups.

    Mirrors ADK's own `_get_model_id` (last path component). This is the firewall
    that keeps the gateway prefix out of `agent_telemetry` and cost
    reconciliation -- without it every pricing lookup misses and cost silently
    becomes zero, with no exception anywhere.
    """
    text = str(model or "")
    return text.rsplit("/", 1)[-1] if text.startswith("apigee/") else text


def gateway_identity_headers() -> dict[str, str]:
    """Per-user attribution headers, or `{}` when nothing is in scope.

    Apply these **per request**, never at client construction -- see this
    module's docstring. Blank values are omitted rather than replaced with
    sentinels so Apigee's own `AM-Identity` defaults apply.
    """
    identity = current_llm_identity()
    if identity is None:
        return {}
    headers: dict[str, str] = {}
    if identity.oid:
        headers[HEADER_USER_OID] = identity.oid
    if identity.department:
        headers[HEADER_USER_DEPARTMENT] = identity.department
    return headers


def gateway_request_http_options():
    """Per-request `HttpOptions` carrying identity, or None when there is none.

    Safe to merge into a `GenerateContentConfig`: google-genai patches
    per-request options over the client's, and for every field except `headers`
    a `None` falls back to the client value -- so `base_url`, the resource
    scope, `timeout` and `retry_options` all survive. Headers are merged rather
    than replaced, so the client-level `x-apikey` survives too.
    """
    headers = gateway_identity_headers()
    if not headers:
        return None
    from google.genai import types as genai_types

    return genai_types.HttpOptions(headers=headers)


def gateway_credential_headers() -> dict[str, str]:
    """The credential header a client must be constructed with.

    Separate from `gateway_client_kwargs` because `ApigeeLlm` takes headers
    through its `custom_headers` constructor argument rather than an
    `HttpOptions` object. Credential only -- identity is per request.
    """
    return {"x-apikey": _require_api_key()}


def gateway_client_kwargs() -> dict[str, object]:
    """Connection-shape `HttpOptions` kwargs. Never identity.

    Returns `{}` when the gateway is disabled, so callers can always write
    `HttpOptions(**base_kwargs, **gateway_client_kwargs())`.
    """
    if not gateway_enabled():
        return {}

    from google.genai import types as genai_types

    return {
        "base_url": gateway_base_url(),
        "base_url_resource_scope": genai_types.ResourceScope.COLLECTION,
        "headers": gateway_credential_headers(),
    }


def gateway_vertex_identity_kwargs(project: str, location: str) -> dict[str, object]:
    """The `project=`/`location=` kwargs for `genai.Client(vertexai=True, ...)`.

    When the gateway is on, both must be None. Passing either makes google-genai
    prepend `projects/{project}/locations/{location}/` to the path -- this
    workload's project and the infra region, neither of which is where inference
    runs -- and re-enables ADC token minting. The proxy's TargetEndpoint
    supplies the real `projects/gclt-aicoe-dev-llm/locations/europe-west3`
    prefix instead.

    When the gateway is off this returns the given values unchanged, which is
    the direct-to-Vertex behaviour the existing tests assert.
    """
    if not gateway_enabled():
        return {"project": project, "location": location}
    return {"project": None, "location": None}
