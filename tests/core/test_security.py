import pytest
from unittest.mock import MagicMock, patch
from src.core.security import verify_iap_jwt
from src.core.exceptions import AuthenticationError

@pytest.mark.asyncio
async def test_verify_iap_jwt_no_header():
    request = MagicMock()
    with patch("src.core.security.settings") as mock_s:
        mock_s.AUTH_ENABLED = True
        # verify_iap_jwt expects the second argument to be a string or None
        with pytest.raises(AuthenticationError):
            await verify_iap_jwt(request, x_goog_iap_jwt_assertion=None)

@pytest.mark.asyncio
async def test_verify_iap_jwt_invalid_token():
    request = MagicMock()
    with patch("src.core.security.settings") as mock_s:
        mock_s.AUTH_ENABLED = True
        mock_s.IAP_AUDIENCE = "test-aud"
        
        with patch("google.oauth2.id_token.verify_token") as mock_verify:
            mock_verify.side_effect = Exception("Invalid signature")
            with pytest.raises(AuthenticationError):
                await verify_iap_jwt(request, x_goog_iap_jwt_assertion="dummy")

@pytest.mark.asyncio
async def test_verify_iap_jwt_success():
    request = MagicMock()
    with patch("src.core.security.settings") as mock_s:
        mock_s.AUTH_ENABLED = True
        mock_s.IAP_AUDIENCE = "test-aud"
        
        with patch("google.oauth2.id_token.verify_token") as mock_verify:
            mock_verify.return_value = {"email": "test@user.com"}
            user_info = await verify_iap_jwt(request, x_goog_iap_jwt_assertion="dummy")
            assert user_info["email"] == "test@user.com"

@pytest.mark.asyncio
async def test_verify_iap_jwt_disabled():
    request = MagicMock()
    with patch("src.core.security.settings") as mock_s:
        mock_s.AUTH_ENABLED = False
        
        user_info = await verify_iap_jwt(request, x_goog_iap_jwt_assertion="dummy")
        assert user_info["email"] == "anonymous@colt.net"
