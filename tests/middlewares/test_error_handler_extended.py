from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.exceptions import (
    AuthenticationError,
    DatabaseError,
    InputValidationException,
    ResourceNotFoundError,
    ServiceError,
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
