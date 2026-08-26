from unittest.mock import patch

from src.shared.utils.url_utils import is_authoritative


def test_is_authoritative_trusted_domain():
    assert is_authoritative("https://www.reuters.com/article/123") is True


def test_is_authoritative_untrusted_domain():
    assert is_authoritative("https://example.com/page") is False


def test_is_authoritative_empty_or_invalid():
    assert is_authoritative("") is False
    assert is_authoritative("not-a-url") is False


def test_is_authoritative_returns_false_when_parse_raises():
    with patch(
        "src.shared.utils.url_utils.urlparse", side_effect=ValueError("bad url")
    ):
        assert is_authoritative("https://reuters.com/article") is False
