class SDKError(Exception):
    """Base exception for SDK errors."""


class ValidationError(SDKError):
    """Raised when user input is invalid."""


class NotFoundError(SDKError):
    """Raised when a required resource does not exist."""


class ConflictError(SDKError):
    """Raised when optimistic locking detects conflicting updates."""


class ExternalServiceError(SDKError):
    """Raised when an upstream service call fails."""
