from typing import Any, ClassVar

from fastapi import status


class DomainError(Exception):
    """Base for expected business errors; mapped to a unified JSON error by main.py."""

    status_code: ClassVar[int] = status.HTTP_400_BAD_REQUEST
    default_code: ClassVar[str] = "DOMAIN_ERROR"
    headers: ClassVar[dict[str, str] | None] = None

    def __init__(
        self, message: str, *, code: str | None = None, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.details = details or {}


class AuthenticationError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    default_code = "NOT_AUTHENTICATED"
    headers: ClassVar[dict[str, str] | None] = {"WWW-Authenticate": "Bearer"}


class PermissionDeniedError(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "PERMISSION_DENIED"


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "NOT_FOUND"


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CONFLICT"


class BusinessRuleError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "BUSINESS_RULE_VIOLATION"


class DomainValidationError(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    default_code = "VALIDATION_ERROR"
