from functools import cached_property

from google.adk.models import Gemini
from google.genai import Client
from google.genai import types as genai_types

from src.shared.config import settings
from src.shared.llm_gateway import (
    gateway_http_options_kwargs,
    gateway_vertex_identity_kwargs,
)

# Configuration with full Exponential Backoff and Jitter
retry_config = genai_types.HttpRetryOptions(
    attempts=settings.GEMINI_RETRY_ATTEMPTS,
    initial_delay=settings.GEMINI_RETRY_INITIAL_DELAY,
    max_delay=settings.GEMINI_RETRY_MAX_DELAY,
    exp_base=settings.GEMINI_RETRY_EXP_BASE,
    jitter=settings.GEMINI_RETRY_JITTER,
    http_status_codes=settings.GEMINI_RETRY_STATUS_CODES,
)


class RegionalGemini(Gemini):
    """ADK Gemini model pinned to settings.vertex_ai_location.

    ADK's default Gemini.api_client builds a plain genai.Client() whose
    project/location are resolved from the GOOGLE_CLOUD_PROJECT /
    GOOGLE_CLOUD_LOCATION process env vars (see google.genai._api_client).
    That couples the LLM inference region to the same env var used for
    project-scoped infra (Cloud Tasks queue location, GCS bucket location).
    This subclass follows ADK's own documented customization pattern
    (see google.adk.models.google_llm.Gemini docstring) to instead pin the
    Vertex AI region explicitly via settings.vertex_ai_location, so it can
    differ from settings.GOOGLE_CLOUD_LOCATION (e.g. LLM served from
    europe-west3 while infra remains in europe-west1).

    LLM gateway (Apigee `llm` env): no workload SA in gclt-aicoe-dev-st may
    hold roles/aiplatform.user (D-30), so when LLM_GATEWAY_ENABLED is set
    this points `base_url` at the Apigee `llm` gateway instead of calling
    Vertex directly, and adds the gateway's x-apikey header alongside ADK's
    own tracking headers. Defaults to disabled -- the `llm` proxy does not
    exist yet (GAP-REGISTER R-08) and whether server-side GoogleSearch
    grounding (see the `llm` global below) survives being proxied is
    unverified (WS-E4, highest risk item in the shared-dev plan).
    """

    def _tracking_headers(self) -> dict[str, str]:
        headers = dict(super()._tracking_headers())
        headers.update(gateway_http_options_kwargs().get("headers", {}))
        return headers

    @cached_property
    def api_client(self) -> Client:
        base_url, api_version = self._base_url_and_api_version
        gateway_kwargs = gateway_http_options_kwargs()
        base_url = gateway_kwargs.get("base_url", base_url)
        kwargs_for_http_options: dict = {
            "headers": self._tracking_headers(),
            "retry_options": self.retry_options,
            "base_url": base_url,
        }
        if api_version:
            kwargs_for_http_options["api_version"] = api_version
        return Client(
            vertexai=settings.GOOGLE_GENAI_USE_VERTEXAI,
            **gateway_vertex_identity_kwargs(
                settings.GOOGLE_CLOUD_PROJECT, settings.vertex_ai_location
            ),
            http_options=genai_types.HttpOptions(**kwargs_for_http_options),
        )


llm = RegionalGemini(
    model=settings.GEMINI_MODEL,
    retry_options=retry_config,
    generate_content_config=genai_types.GenerateContentConfig(
        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
    ),
)
