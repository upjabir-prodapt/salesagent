import pytest
from src.utils.safety import get_default_safety_settings, get_safety_config_for_agent
from unittest.mock import patch, MagicMock

def test_get_default_safety_settings_various(mock_settings):
    # Test different threshold strings
    mock_settings.SAFETY_HARASSMENT_THRESHOLD = "BLOCK_LOW_AND_ABOVE"
    mock_settings.SAFETY_HATE_SPEECH_THRESHOLD = "BLOCK_NONE"
    mock_settings.SAFETY_SEXUAL_THRESHOLD = "OFF"
    mock_settings.SAFETY_DANGEROUS_THRESHOLD = "BLOCK_ONLY_HIGH"
    
    # Use create=True to patch things that might not be in environment
    with patch("src.utils.safety.HarmCategory", create=True), \
         patch("src.utils.safety.SafetySetting", create=True):
        
        settings = get_default_safety_settings()
        assert len(settings) == 4

def test_get_safety_config_for_agent(mock_settings):
    mock_settings.SAFETY_HARASSMENT_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_HATE_SPEECH_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_SEXUAL_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    mock_settings.SAFETY_DANGEROUS_THRESHOLD = "BLOCK_MEDIUM_AND_ABOVE"
    
    with patch("src.utils.safety.HarmCategory", create=True), \
         patch("src.utils.safety.SafetySetting", create=True), \
         patch("src.utils.safety.SafetyConfig", create=True):
        
        config = get_safety_config_for_agent("TestAgent")
        assert config is not None
