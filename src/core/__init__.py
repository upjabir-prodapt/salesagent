"""Core module for base exceptions and configuration."""

from .config import settings
from .exceptions import (
    AppException,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DatabaseError,
    ExternalServiceError,
    InputValidationException,
    RateLimitException,
    RepositoryError,
    ResourceNotFoundError,
    SafetyBlockException,
    ServiceError,
    StorageError,
    TimeoutException,
    ValidationError,
)

__all__ = [
    "settings",
    # Legacy exceptions
    "AppException",
    "DatabaseError",
    "StorageError",
    "ResourceNotFoundError",
    "ValidationError",
    "AuthenticationError",
    "AuthorizationError",
    "ExternalServiceError",
    "ConfigurationError",
    # New exceptions
    "ServiceError",
    "RepositoryError",
    "SafetyBlockException",
    "InputValidationException",
    "TimeoutException",
    "RateLimitException",
]
