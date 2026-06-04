from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.exceptions import (
    AuthenticationError,
    AuthorizationError,
    DatabaseError,
    ExternalServiceError,
    InputValidationException,
    RateLimitException,
    ResourceNotFoundError,
    SafetyBlockException,
    ServiceError,
    TimeoutException,
)
from src.middlewares.error_handler import error_handler_middleware

app = FastAPI()
error_handler_middleware(app)


@app.get("/auth-error")
async def trigger_auth_error():
    raise AuthenticationError("Auth failed")


@app.get("/not-found-error")
async def trigger_not_found_error():
    raise ResourceNotFoundError("Not found")


@app.get("/service-error")
async def trigger_service_error():
    raise ServiceError("Service failed")


@app.get("/validation-error")
async def trigger_validation_error():
    raise InputValidationException("Invalid input")


@app.get("/db-error")
async def trigger_db_error():
    raise DatabaseError("DB failed")


@app.get("/safety-error")
async def trigger_safety_error():
    raise SafetyBlockException("Blocked", categories=["HARM"])


@app.get("/authz-error")
async def trigger_authz_error():
    raise AuthorizationError("Forbidden")


@app.get("/rate-limit-error")
async def trigger_rate_limit_error():
    raise RateLimitException("Too many requests", retry_after=30)


@app.get("/timeout-error")
async def trigger_timeout_error():
    raise TimeoutException("Timed out")


@app.get("/external-error")
async def trigger_external_error():
    raise ExternalServiceError("Upstream failed")


def test_error_handler_functional():
    client = TestClient(app)

    # Test Auth Error
    response = client.get("/auth-error")
    assert response.status_code == 401
    assert "Auth failed" in response.json()["detail"]

    # Test Not Found
    response = client.get("/not-found-error")
    assert response.status_code == 404

    # Test Service Error
    response = client.get("/service-error")
    assert response.status_code == 500

    # Test Validation
    response = client.get("/validation-error")
    assert response.status_code == 400

    # Test DB Error
    response = client.get("/db-error")
    assert response.status_code == 500

    response = client.get("/safety-error")
    assert response.status_code == 400
    assert response.json()["error"] == "SAFETY_VIOLATION"

    response = client.get("/authz-error")
    assert response.status_code == 403

    response = client.get("/rate-limit-error")
    assert response.status_code == 429
    assert response.json()["metadata"]["retry_after"] == 30

    response = client.get("/timeout-error")
    assert response.status_code == 504

    response = client.get("/external-error")
    assert response.status_code == 502
