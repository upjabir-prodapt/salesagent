"""The ADK model this service's agents run on.

Every LLM call goes through the Apigee `llm` gateway: no workload service
account in `gclt-aicoe-dev-st` holds `roles/aiplatform.user` (LLD D-30), so a
direct Vertex call returns 403 regardless of what the code does.
"""

from collections.abc import AsyncGenerator
from functools import cached_property

from google.adk.models import Gemini
from google.adk.models.apigee_llm import ApigeeLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import Client
from google.genai import types as genai_types

from src.shared.config import settings
from src.shared.llm_gateway import (
    apigee_model_string,
    gateway_base_url,
    gateway_credential_headers,
    gateway_enabled,
    gateway_identity_headers,
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

    The direct-to-Vertex path, used only when the gateway is disabled (local
    development). ADK's default `Gemini.api_client` resolves project/location
    from the `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` process env vars,
    which couples the LLM inference region to the region used for project-scoped
    infra (Cloud Tasks, GCS, BigQuery). This subclass pins the Vertex region
    explicitly so inference can run in europe-west3 while infra stays in
    europe-west1.
    """

    @cached_property
    def api_client(self) -> Client:
        base_url, api_version = self._base_url_and_api_version
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


class ApigeeRegionalLlm(ApigeeLlm):
    """`ApigeeLlm` with the project/location prefix suppressed and per-call identity.

    Two things have to be corrected about the stock class.

    1. **The request path.** `ApigeeLlm.api_client` passes `project` and
       `location` (read from the environment, and not overridable through its
       constructor) into `genai.Client`. google-genai then prepends
       `projects/{project}/locations/{location}/` to the path -- this workload's
       project and the *infra* region, neither of which is where inference runs.
       Worse, without `base_url_resource_scope=COLLECTION` google-genai drops
       the path entirely for a custom base URL. So this override passes
       `project=None, location=None` AND sets the resource scope. Measured
       against google-genai 1.75.0:

           stock ApigeeLlm : .../v1beta1/projects/gclt-aicoe-dev-st/locations/europe-west1/publishers/...
           this override   : .../v1/publishers/google/models/<model>:generateContent

       Only the second matches the gateway's model allow-list, which Apigee
       evaluates *before* any policy in the proxy runs.

    2. **Identity cannot live on the client.** `api_client` is a
       `cached_property`, so headers set at construction are frozen for the life
       of the object. Attribution therefore rides on each request, stamped onto
       `llm_request.config.http_options` in `generate_content_async`.
       google-genai merges per-request headers over the client's, and falls back
       to client values for every other field, so the base URL, resource scope,
       retry options and the `x-apikey` credential all survive.

    The `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` env vars are still read
    by `ApigeeLlm.__init__`, which raises if either is unset even though this
    override discards both -- see `config.py`'s `_sync_sdk_environment`, which
    exports them explicitly rather than relying on `load_dotenv` having done it.
    """

    @cached_property
    def api_client(self) -> Client:
        return Client(
            vertexai=True,
            project=None,
            location=None,
            http_options=genai_types.HttpOptions(
                base_url=gateway_base_url(),
                # Without COLLECTION the path is discarded entirely and the SDK
                # POSTs to the bare host -- see src/shared/llm_gateway.py.
                base_url_resource_scope=genai_types.ResourceScope.COLLECTION,
                headers=self._merge_tracking_headers(self._custom_headers),
                retry_options=self.retry_options,
                timeout=int(settings.GENAI_HTTP_TIMEOUT_SECONDS * 1000),
            ),
        )

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        """Stamp the calling user onto this request, then delegate.

        Per request rather than per client -- see the class docstring. When no
        identity is in scope the headers are omitted entirely so Apigee applies
        its own `system` / `unattributed` defaults.
        """
        headers = gateway_identity_headers()
        if headers:
            config = llm_request.config or genai_types.GenerateContentConfig()
            http_options = config.http_options or genai_types.HttpOptions()
            http_options.headers = {**(http_options.headers or {}), **headers}
            config.http_options = http_options
            llm_request.config = config
        async for response in super().generate_content_async(llm_request, stream):
            yield response


def build_llm(model: str, retry_options=None):
    """The ADK model for `model`, routed through the gateway when it is enabled.

    `model` is the BARE name (e.g. `gemini-3.5-flash`). The `apigee/vertex_ai/`
    prefix is applied here and only here -- it must never reach the pricing
    registry or cost/telemetry recording, where it would silently miss every
    lookup and zero the cost. `AdkAgentStep.execute` strips it back off before
    reporting usage.
    """
    retry = retry_options if retry_options is not None else retry_config
    if not gateway_enabled():
        return RegionalGemini(model=model, retry_options=retry)
    # custom_headers is how ApigeeLlm accepts client headers -- it takes no
    # HttpOptions. Without it there is no x-apikey on the wire and the proxy's
    # VA-ApiKey policy rejects every call with 401.
    return ApigeeRegionalLlm(
        model=apigee_model_string(model),
        proxy_url=gateway_base_url(),
        custom_headers=gateway_credential_headers(),
        retry_options=retry,
    )
