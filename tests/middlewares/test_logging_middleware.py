import pytest
from fastapi import Request, Response
from unittest.mock import MagicMock, AsyncMock
from src.middlewares.logging_middleware import LoggingMiddleware

@pytest.mark.asyncio
async def test_logging_middleware_success():
    app = MagicMock()
    middleware = LoggingMiddleware(app)
    
    request = MagicMock(spec=Request)
    request.method = "GET"
    request.url.path = "/test"
    request.headers = {}
    request.state = MagicMock()
    
    async def call_next(req):
        return Response(status_code=200)
    
    # We need to mock the dispatch method or the whole chain
    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 200
