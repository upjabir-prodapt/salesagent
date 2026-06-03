from src.core.exceptions import (
    AgentOutputError,
    AppException,
    AuthenticationError,
    BaseAppException,
    DatabaseError,
    InputValidationException,
    RepositoryError,
    ResourceNotFoundError,
    ServiceError,
    StorageError,
    ValidationError,
)


def test_base_app_exception_fields():
    exc = BaseAppException("boom", status_code=418, error_type="Teapot")
    assert exc.message == "boom"
    assert exc.status_code == 418
    assert str(exc) == "boom"


def test_typed_exceptions_defaults():
    assert ServiceError("svc").error_type == "Service Error"
    assert DatabaseError("db").status_code == 500
    assert StorageError("gcs").error_type == "Storage Error"
    assert ResourceNotFoundError("missing").status_code == 404
    assert ValidationError("bad").status_code == 400
    assert AuthenticationError("auth").status_code == 401
    assert RepositoryError("repo").error_type == "Repository Error"
    assert AppException().message == "An application error occurred"
    assert InputValidationException("x").error_type == "Input Validation Error"
    assert (
        AgentOutputError("agent", agent_name="a1", output_key="out").agent_name == "a1"
    )
