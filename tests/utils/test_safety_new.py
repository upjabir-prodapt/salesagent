from unittest.mock import MagicMock, patch

from google.genai import types

from src.services.research.agent.utils.safety import (
    analyze_safety_block,
    create_safety_summary,
    format_safety_ratings,
    get_business_research_safety_settings,
    get_default_safety_settings,
    get_safety_config_for_agent,
    is_safety_block,
    log_safety_event,
)


def test_get_default_safety_settings(mock_settings):
    mock_settings.SAFETY_HARASSMENT_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_HATE_SPEECH_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_SEXUAL_THRESHOLD = "BLOCK_LOW_AND_ABOVE"
    mock_settings.SAFETY_DANGEROUS_THRESHOLD = "BLOCK_ONLY_HIGH"

    with patch(
        "src.services.research.agent.utils.safety.get_default_safety_settings",
        wraps=get_default_safety_settings,
    ), patch("src.core.config.settings", mock_settings):
        # Need to patch the settings used INSIDE the function if it's imported there
        settings = get_default_safety_settings()
        assert len(settings) == 4
        for s in settings:
            assert isinstance(s, types.SafetySetting)


def test_get_business_research_safety_settings():
    settings = get_business_research_safety_settings()
    assert len(settings) == 4
    # Check one specific threshold
    for s in settings:
        if s.category == types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT:
            assert s.threshold == types.HarmBlockThreshold.BLOCK_ONLY_HIGH


def test_format_safety_ratings():
    rating1 = MagicMock()
    rating1.category = "HARM_CATEGORY_HATE_SPEECH"
    rating1.probability = "NEGLIGIBLE"
    rating1.blocked = False

    rating2 = MagicMock()
    rating2.category = "HARM_CATEGORY_HARASSMENT"
    rating2.probability = "MEDIUM"
    rating2.blocked = True

    formatted = format_safety_ratings([rating1, rating2])
    assert "HATE_SPEECH: NEGLIGIBLE" in formatted
    assert "HARASSMENT: MEDIUM [BLOCKED]" in formatted

    assert format_safety_ratings([]) == "No safety ratings available"


def test_analyze_safety_block():
    candidate = MagicMock()
    candidate.finish_reason = "SAFETY"

    rating = MagicMock()
    rating.category = "HARM_CATEGORY_SEXUALLY_EXPLICIT"
    rating.probability = "HIGH"
    rating.blocked = True

    candidate.safety_ratings = [rating]

    analysis = analyze_safety_block(candidate, "req_123")
    assert analysis["blocked"] is True
    assert analysis["reason"] == "SAFETY"
    assert "SEXUALLY_EXPLICIT" in analysis["categories"]
    assert analysis["highest_probability"] == "HIGH"
    assert analysis["request_id"] == "req_123"


def test_analyze_safety_block_no_ratings():
    candidate = MagicMock()
    candidate.finish_reason = "OTHER"
    candidate.safety_ratings = None

    analysis = analyze_safety_block(candidate)
    assert analysis["blocked"] is False
    assert analysis["highest_probability"] == "NEGLIGIBLE"


def test_is_safety_block():
    event = MagicMock()
    candidate = MagicMock()
    candidate.finish_reason = "SAFETY"
    event.candidates = [candidate]

    assert is_safety_block(event) is True

    candidate.finish_reason = "STOP"
    assert is_safety_block(event) is False

    del event.candidates
    assert is_safety_block(event) is False


def test_log_safety_event(mock_settings):
    with patch("src.services.research.agent.utils.safety.logger") as mock_logger:
        mock_settings.SAFETY_LOGGING_ENABLED = True
        log_safety_event("BLOCK", {"info": "test"}, level="ERROR")
        mock_logger.error.assert_called()

        log_safety_event("BLOCK", {"info": "test"}, level="DEBUG")
        mock_logger.debug.assert_called()

        log_safety_event("BLOCK", {"info": "test"}, level="INFO")
        mock_logger.info.assert_called()

        log_safety_event("BLOCK", {"info": "test"}, level="WARNING")
        mock_logger.warning.assert_called()

        log_safety_event("BLOCK", {"info": "test"}, level="UNKNOWN")
        # should default to info
        mock_logger.info.assert_called()


def test_log_safety_event_disabled(mock_settings):
    with patch("src.services.research.agent.utils.safety.logger") as mock_logger:
        mock_settings.SAFETY_LOGGING_ENABLED = False
        log_safety_event("BLOCK", {"info": "test"})
        mock_logger.warning.assert_not_called()


def test_create_safety_summary():
    events = [
        {"blocked": True, "ratings": [{"category": "HATE", "probability": "HIGH"}]},
        {
            "blocked": False,
            "ratings": [{"category": "DANGEROUS", "probability": "LOW"}],
        },
    ]
    summary = create_safety_summary(events)
    assert summary["total_events"] == 2
    assert summary["blocked_count"] == 1
    assert summary["categories_triggered"]["HATE"] == 1
    assert summary["highest_severity"] == "HIGH"


def test_get_safety_config_for_agent(mock_settings):
    mock_settings.SAFETY_HARASSMENT_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_HATE_SPEECH_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_SEXUAL_THRESHOLD = "BLOCK_LOW_AND_ABOVE"
    mock_settings.SAFETY_DANGEROUS_THRESHOLD = "BLOCK_ONLY_HIGH"

    with patch("src.core.config.settings", mock_settings):
        config = get_safety_config_for_agent("TestAgent")
        assert isinstance(config, types.GenerateContentConfig)
        assert len(config.safety_settings) == 4
