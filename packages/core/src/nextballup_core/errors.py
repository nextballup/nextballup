from __future__ import annotations

from typing import Any

from nextballup_core.constants import ErrorCode


class AppError(Exception):
    """Base application error. Carries the HTTP status, code, message, and details
    that the API exception handler renders in the standard error envelope."""

    status_code: int = 400
    code: str = ErrorCode.VALIDATION_FAILED

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details: dict[str, Any] = details or {}


class ValidationFailedError(AppError):
    status_code = 422
    code = ErrorCode.VALIDATION_FAILED


class AuthenticationError(AppError):
    status_code = 401
    code = ErrorCode.UNAUTHENTICATED


class InvalidCredentialsError(AuthenticationError):
    code = ErrorCode.INVALID_CREDENTIALS


class ForbiddenError(AppError):
    status_code = 403
    code = ErrorCode.FORBIDDEN


class NotFoundError(AppError):
    status_code = 404
    code = ErrorCode.NOT_FOUND


class ConflictError(AppError):
    status_code = 409
    code = ErrorCode.EMAIL_TAKEN


class TooManyRequestsError(AppError):
    status_code = 429
    code = ErrorCode.RATE_LIMITED


class ServiceUnavailableError(AppError):
    status_code = 503
    code = ErrorCode.INTERNAL_ERROR
