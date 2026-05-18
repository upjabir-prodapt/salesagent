from unittest.mock import MagicMock, patch

import pytest

from src.utils.safety import get_default_safety_settings, get_safety_config_for_agent


@pytest.fixture
def mock_types():
    with patch("src.utils.safety.types") as mock:
        # HarmCategory
        mock.HarmCategory.HARM_CATEGORY_HARASSMENT = "HARASSMENT"
        mock.HarmCategory.HARM_CATEGORY_HATE_SPEECH = "HATE_SPEECH"
        mock.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT = "SEXUAL"
        mock.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT = "DANGEROUS"

        # HarmBlockThreshold
        mock.HarmBlockThreshold.BLOCK_NONE = "BLOCK_NONE"
        mock.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE = "BLOCK_LOW_AND_ABOVE"
        mock.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE = "BLOCK_MEDIUM_AND_ABOVE"
        mock.HarmBlockThreshold.BLOCK_ONLY_HIGH = "BLOCK_ONLY_HIGH"

        # Mock SafetySetting class
        def safety_setting_init(category, threshold):
            m = MagicMock()
            m.category = category
            m.threshold = threshold
            return m

        mock.SafetySetting.side_effect = safety_setting_init

        yield mock


def test_get_default_safety_settings_exhaustive(mock_settings, mock_types):
    test_cases = [
        ("BLOCK_NONE", "BLOCK_NONE"),
        ("BLOCK_LOW_AND_ABOVE", "BLOCK_LOW_AND_ABOVE"),
        ("BLOCK_MEDIUM_AND_ABOVE", "BLOCK_MEDIUM_AND_ABOVE"),
        ("BLOCK_ONLY_HIGH", "BLOCK_ONLY_HIGH"),
    ]

    for input_str, expected_enum in test_cases:
        mock_settings.SAFETY_HARASSMENT_THRESHOLD = input_str
        mock_settings.SAFETY_HATE_SPEECH_THRESHOLD = input_str
        mock_settings.SAFETY_SEXUAL_THRESHOLD = input_str
        mock_settings.SAFETY_DANGEROUS_THRESHOLD = input_str

        settings = get_default_safety_settings()
        assert len(settings) == 4
        for s in settings:
            assert s.threshold == expected_enum


def test_get_safety_config_for_agent(mock_settings, mock_types):
    mock_settings.SAFETY_HARASSMENT_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_HATE_SPEECH_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_SEXUAL_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_DANGEROUS_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"

    config = get_safety_config_for_agent("TestAgent")
    assert config is not None
