"""Regression tests for RegionalGemini (src/worker/model.py).

Verifies that the Vertex AI inference region (settings.vertex_ai_location)
can be configured independently of the project's infra region
(settings.GOOGLE_CLOUD_LOCATION, used for Cloud Tasks/GCS/BigQuery), per
the user's requirement to keep project location at europe-west1 while
Gemini inference runs in europe-west3.
"""

from __future__ import annotations

from unittest.mock import patch

from src.shared.config import settings
from src.worker.model import RegionalGemini


def test_vertex_ai_location_falls_back_to_google_cloud_location():
    with (
        patch.object(settings, "GOOGLE_CLOUD_LOCATION", "europe-west1"),
        patch.object(settings, "VERTEX_AI_LOCATION", ""),
    ):
        assert settings.vertex_ai_location == "europe-west1"


def test_vertex_ai_location_overrides_google_cloud_location():
    with (
        patch.object(settings, "GOOGLE_CLOUD_LOCATION", "europe-west1"),
        patch.object(settings, "VERTEX_AI_LOCATION", "europe-west3"),
    ):
        assert settings.vertex_ai_location == "europe-west3"


def test_regional_gemini_api_client_uses_vertex_ai_location():
    with (
        patch.object(settings, "GOOGLE_CLOUD_LOCATION", "europe-west1"),
        patch.object(settings, "VERTEX_AI_LOCATION", "europe-west3"),
    ):
        model = RegionalGemini(model="gemini-3.5-flash")
        client = model.api_client
        assert client._api_client.location == "europe-west3"
        assert client._api_client.project == settings.GOOGLE_CLOUD_PROJECT


def test_regional_gemini_defaults_when_vertex_ai_location_unset():
    with (
        patch.object(settings, "GOOGLE_CLOUD_LOCATION", "europe-west1"),
        patch.object(settings, "VERTEX_AI_LOCATION", ""),
    ):
        model = RegionalGemini(model="gemini-3.5-flash")
        client = model.api_client
        assert client._api_client.location == "europe-west1"


def test_llm_model_info_resolves_pricing_from_vertex_ai_location():
    with (
        patch.object(settings, "GOOGLE_CLOUD_LOCATION", "europe-west1"),
        patch.object(settings, "VERTEX_AI_LOCATION", "europe-west3"),
    ):
        info = settings.llm_model_info
        assert info.region == "europe-west3"
