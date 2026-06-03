from src.models.common_schemas import ErrorResponse


def test_error_response_minimal():
    model = ErrorResponse(error="Bad Request", detail="Invalid input")
    assert model.error == "Bad Request"
    assert model.request_id is None


def test_error_response_full():
    model = ErrorResponse(
        error="Validation Failed",
        detail="Company name cannot be empty.",
        request_id="req-1",
        timestamp="2026-01-01T00:00:00Z",
        metadata={"field": "company_name"},
    )
    dumped = model.model_dump()
    assert dumped["metadata"]["field"] == "company_name"
