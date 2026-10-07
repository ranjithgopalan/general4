"""
Exception types and error codes for the AIDLC Platform Agents.

A single ``BaseAppException`` carries a machine-readable ``error_code``, a human
message, optional ``details`` and the HTTP ``status_code``. The FastAPI handlers in
``app.main`` render these consistently with a request/correlation id.
"""

from enum import Enum
from typing import Any

from fastapi import status


class ErrorCode(str, Enum):
    """Stable error codes for consistent reporting and client handling."""

    # AuthN / AuthZ
    INVALID_TOKEN = "INVALID_TOKEN"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    INVALID_API_KEY = "INVALID_API_KEY"
    ENTITLEMENT_ERROR = "ENTITLEMENT_ERROR"

    # Validation / resources
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    NOT_FOUND = "NOT_FOUND"
    RESOURCE_CONFLICT = "RESOURCE_CONFLICT"

    # External services
    VAULT_ERROR = "VAULT_ERROR"
    LLM_ERROR = "LLM_ERROR"
    SOURCE_ERROR = "SOURCE_ERROR"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"

    # FE moat (grounding spine)
    GROUNDING_FAILED = "GROUNDING_FAILED"
    COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"

    # Internal
    INTERNAL_SERVER_ERROR = "INTERNAL_SERVER_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"


class BaseAppException(Exception):
    """Base for all application exceptions."""

    def __init__(
        self,
        error_code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
    ):
        self.error_code = error_code
        self.message = message
        self.details = details or {}
        self.status_code = status_code
        super().__init__(self.message)


# ── AuthN / AuthZ ──────────────────────────────────────────────────────────────
class AuthenticationError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.UNAUTHORIZED, message, details, status.HTTP_401_UNAUTHORIZED)


class AuthorizationError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.FORBIDDEN, message, details, status.HTTP_403_FORBIDDEN)


class EntitlementError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.ENTITLEMENT_ERROR, message, details, status.HTTP_403_FORBIDDEN)


class ApiKeyError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.INVALID_API_KEY, message, details, status.HTTP_401_UNAUTHORIZED)


# ── Validation / resources ──────────────────────────────────────────────────────
class ValidationError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.VALIDATION_ERROR, message, details, status.HTTP_422_UNPROCESSABLE_ENTITY)


class ResourceNotFoundError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.NOT_FOUND, message, details, status.HTTP_404_NOT_FOUND)


# ── External services ─────────────────────────────────────────────────────────
class VaultError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.VAULT_ERROR, message, details, status.HTTP_502_BAD_GATEWAY)


class SourceError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.SOURCE_ERROR, message, details, status.HTTP_502_BAD_GATEWAY)


# ── FE moat / internal ──────────────────────────────────────────────────────────
class GroundingError(BaseAppException):
    """A generated artifact failed the grounding gate (cite-or-abstain)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.GROUNDING_FAILED, message, details, status.HTTP_422_UNPROCESSABLE_ENTITY)


class ConfigurationError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.CONFIGURATION_ERROR, message, details, status.HTTP_500_INTERNAL_SERVER_ERROR)


class ServiceUnavailableError(BaseAppException):
    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(ErrorCode.SERVICE_UNAVAILABLE, message, details, status.HTTP_503_SERVICE_UNAVAILABLE)
