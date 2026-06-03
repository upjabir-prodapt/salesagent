from unittest.mock import patch

import pytest
from fastapi import HTTPException
from jwt import InvalidTokenError

from src.core.security import (
    create_access_token,
    decode_and_verify_token,
    extract_bearer_token,
)


def test_extract_bearer_token_variants():
    assert extract_bearer_token(None) is None
    assert extract_bearer_token("") is None
    assert extract_bearer_token("   ") is None
    assert extract_bearer_token("raw-token") == "raw-token"
    assert extract_bearer_token("Bearer my.jwt.token") == "my.jwt.token"


def test_decode_and_verify_token_success(mock_settings):
    token = create_access_token(
        {
            "sub": "user@colt.net",
            "business_unit": "Sales",
            "organization": "Colt",
        }
    )
    payload = decode_and_verify_token(token)
    assert payload["sub"] == "user@colt.net"


def test_decode_and_verify_token_invalid(mock_settings):
    with pytest.raises(HTTPException) as exc_info:
        decode_and_verify_token("not-a-jwt")
    assert exc_info.value.status_code == 401


def test_decode_and_verify_token_missing_claims(mock_settings):
    token = create_access_token({"sub": "user@colt.net"})
    with pytest.raises(HTTPException) as exc_info:
        decode_and_verify_token(token)
    assert "Missing required token claims" in exc_info.value.detail


def test_decode_and_verify_token_jwt_error(mock_settings):
    with (
        patch("src.core.security.jwt.decode", side_effect=InvalidTokenError("bad")),
        pytest.raises(HTTPException) as exc_info,
    ):
        decode_and_verify_token("bad.token.value")
    assert exc_info.value.status_code == 401
