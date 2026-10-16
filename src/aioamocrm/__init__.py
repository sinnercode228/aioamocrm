"""Async client for the amoCRM / Kommo API v4."""

from ._errors import (
    AmoCRMError,
    AuthError,
    ForbiddenError,
    NetworkError,
    NotFoundError,
    PaymentRequiredError,
    RateLimitError,
    ServerError,
    ValidationError,
    ValidationIssue,
)

__version__ = "0.1.0"

__all__ = [
    "AmoCRMError",
    "AuthError",
    "ForbiddenError",
    "NetworkError",
    "NotFoundError",
    "PaymentRequiredError",
    "RateLimitError",
    "ServerError",
    "ValidationError",
    "ValidationIssue",
    "__version__",
]
