from unittest.mock import MagicMock

import pytest
from fastapi import Request

from src.shared.exceptions import ServiceError, ValidationError
from src.shared.middlewares.error_handler import GlobalErrorHandler


@pytest.mark.asyncio
async def test_error_handler_validation_error():
    async def call_next(request):
        raise ValidationError("Invalid input")

    app = MagicMock()
    middleware = GlobalErrorHandler(app)
    request = MagicMock(spec=Request)
    request.url.path = "/test"

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_error_handler_service_error():
    async def call_next(request):
        # ServiceError might need a specific status_code set or it uses default
        err = ServiceError("Service down")
        err.status_code = 503
        raise err

    app = MagicMock()
    middleware = GlobalErrorHandler(app)
    request = MagicMock(spec=Request)
    request.url.path = "/test"

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 503
