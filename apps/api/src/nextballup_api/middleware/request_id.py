from __future__ import annotations

import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from nextballup_core.constants import REQUEST_ID_HEADER

_request_id_ctx: ContextVar[str | None] = ContextVar("nbu_request_id", default=None)
_SAFE_REQUEST_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


def current_request_id() -> str | None:
    return _request_id_ctx.get()


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Honours an inbound X-Request-ID header (e.g. from a load balancer) or
    issues a fresh UUIDv4. Stores the id in a contextvar and echoes it back."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = (
            incoming
            if incoming
            and len(incoming) <= 64
            and all(char in _SAFE_REQUEST_ID_CHARS for char in incoming)
            else str(uuid.uuid4())
        )
        token = _request_id_ctx.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            _request_id_ctx.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
