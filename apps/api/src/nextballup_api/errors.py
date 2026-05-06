from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from nextballup_core.constants import ErrorCode
from nextballup_core.errors import AppError

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) else None


def _envelope(
    *,
    code: str,
    message: str,
    request: Request,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {"code": code, "message": message, "details": details or {}}
    }
    request_id = _request_id(request)
    if request_id:
        payload["request_id"] = request_id
    return payload


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(code=exc.code, message=exc.message, request=request, details=exc.details),
    )


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # exc.errors() may include non-serializable `ctx` payloads (e.g. raw
    # ValueError instances raised by Pydantic validators). Strip ctx down to
    # primitives so JSON encoding always succeeds.
    safe_errors = []
    for err in exc.errors():
        cleaned = {k: v for k, v in err.items() if k != "ctx"}
        ctx = err.get("ctx")
        if isinstance(ctx, dict):
            cleaned["ctx"] = {k: str(v) for k, v in ctx.items()}
        safe_errors.append(cleaned)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=_envelope(
            code=ErrorCode.VALIDATION_FAILED,
            message="Request validation failed",
            request=request,
            details={"errors": safe_errors},
        ),
    )


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _status_code_to_error_code(exc.status_code)
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(code=code, message=message, request=request),
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=_envelope(
            code=ErrorCode.INTERNAL_ERROR,
            message="Internal server error",
            request=request,
        ),
    )


def _status_code_to_error_code(status_code: int) -> str:
    return {
        400: ErrorCode.VALIDATION_FAILED,
        401: ErrorCode.UNAUTHENTICATED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.METHOD_NOT_ALLOWED,
        409: ErrorCode.EMAIL_TAKEN,
        429: ErrorCode.RATE_LIMITED,
        422: ErrorCode.VALIDATION_FAILED,
    }.get(status_code, ErrorCode.INTERNAL_ERROR)


def register_exception_handlers(app: FastAPI) -> None:
    handler_app: Callable[[Request, AppError], Awaitable[JSONResponse]] = _app_error_handler
    handler_val: Callable[[Request, RequestValidationError], Awaitable[JSONResponse]] = (
        _validation_error_handler
    )
    handler_http: Callable[[Request, StarletteHTTPException], Awaitable[JSONResponse]] = (
        _http_exception_handler
    )
    handler_unhandled: Callable[[Request, Exception], Awaitable[JSONResponse]] = (
        _unhandled_exception_handler
    )

    app.add_exception_handler(AppError, handler_app)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handler_val)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, handler_http)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, handler_unhandled)
