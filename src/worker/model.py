from functools import cached_property

from google.adk.models import Gemini
from google.genai import Client
from google.genai import types as genai_types

from src.shared.config import settings

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
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.vertex_ai_location,
            http_options=genai_types.HttpOptions(**kwargs_for_http_options),
        )


llm = RegionalGemini(
    model=settings.GEMINI_MODEL,
    retry_options=retry_config,
    generate_content_config=genai_types.GenerateContentConfig(
        tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())]
    ),
)
