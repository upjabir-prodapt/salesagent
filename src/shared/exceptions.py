"""Custom Exceptions for Colt-AI Application"""


class BaseAppException(Exception):
    """Base exception for all application-specific errors."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        error_type: str = "Application Error",
    ):
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        super().__init__(self.message)


class AppException(BaseAppException):
    """Legacy base exception class"""

    def __init__(
        self,
        message: str = "An application error occurred",
        status_code: int = 500,
        error_type: str = "Application Error",
    ):
        super().__init__(message, status_code, error_type)


# Core exceptions
class ServiceError(BaseAppException):
    """Exception raised when a service operation fails"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message, status_code=status_code, error_type="Service Error")


class RepositoryError(BaseAppException):
    """Exception raised when a repository operation fails"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(
            message, status_code=status_code, error_type="Repository Error"
        )


class DatabaseError(BaseAppException):
    """Exception raised for database operations"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message, status_code=status_code, error_type="Database Error")


class StorageError(BaseAppException):
    """Exception raised for storage operations"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message, status_code=status_code, error_type="Storage Error")


class ResourceNotFoundError(BaseAppException):
    """Exception raised when a requested resource is not found"""

    def __init__(self, message: str, status_code: int = 404):
        super().__init__(message, status_code=status_code, error_type="Not Found")


class ValidationError(BaseAppException):
    """Exception raised for validation errors"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(
            message, status_code=status_code, error_type="Validation Error"
        )


class AuthenticationError(BaseAppException):
    """Exception raised for authentication errors"""

    def __init__(self, message: str, status_code: int = 401):
        super().__init__(
            message, status_code=status_code, error_type="Authentication Error"
        )


class AuthorizationError(BaseAppException):
    """Exception raised for authorization errors"""

    def __init__(self, message: str, status_code: int = 403):
        super().__init__(
            message, status_code=status_code, error_type="Authorization Error"
        )


class ExternalServiceError(BaseAppException):
    """Exception raised when external service calls fail"""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(
            message, status_code=status_code, error_type="External Service Error"
        )


class ConfigurationError(BaseAppException):
    """Exception raised for configuration errors"""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(
            message, status_code=status_code, error_type="Configuration Error"
        )


# Guardrail-Specific Exceptions
class SafetyBlockException(BaseAppException):
    """
    Exception raised when content is blocked by safety guardrails.
    """

    def __init__(
        self,
        message: str = "Content blocked by safety guardrails",
        categories: list[str] | None = None,
        request_id: str | None = None,
        safety_ratings: list | None = None,
        status_code: int = 400,
    ):
        self.categories = categories or []
        self.request_id = request_id
        self.safety_ratings = safety_ratings or []
        super().__init__(message, status_code=status_code, error_type="Safety Block")


class InputValidationException(BaseAppException):
    """
    Exception raised when input validation fails.
    """

    def __init__(
        self,
        message: str,
        field: str | None = None,
        value: str | None = None,
        status_code: int = 400,
    ):
        self.field = field
        self.value = value
        super().__init__(
            message, status_code=status_code, error_type="Input Validation Error"
        )


class AgentOutputError(ServiceError):
    """Raised when a pipeline agent finishes without writing its required session output."""

    def __init__(
        self,
        message: str,
        *,
        agent_name: str,
        output_key: str,
        error_class: str | None = None,
    ):
        self.agent_name = agent_name
        self.output_key = output_key
        self.error_class = error_class or "MISSING_OUTPUT"
        super().__init__(message)


class OutputValidationException(BaseAppException):
    """
    Exception raised when output guardrails block a generated report after all retries.
    """

    def __init__(
        self,
        message: str,
        violations: list[str] | None = None,
        status_code: int = 422,
    ):
        self.violations = violations or []
        super().__init__(
            message, status_code=status_code, error_type="Output Validation Error"
        )


class RateLimitException(BaseAppException):
    """
    Exception raised when rate limit is exceeded.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        retry_after: int | None = None,
        limit: int | None = None,
        status_code: int = 429,
    ):
        self.retry_after = retry_after
        self.limit = limit
        super().__init__(
            message, status_code=status_code, error_type="Rate Limit Exceeded"
        )


class TimeoutException(BaseAppException):
    """
    Exception raised when an operation times out.
    """

    def __init__(
        self,
        message: str = "Operation timed out",
        timeout_seconds: int | None = None,
        operation: str | None = None,
        status_code: int = 504,
    ):
        self.timeout_seconds = timeout_seconds
        self.operation = operation
        super().__init__(message, status_code=status_code, error_type="Timeout Error")
