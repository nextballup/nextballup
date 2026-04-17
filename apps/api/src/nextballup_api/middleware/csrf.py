"""CSRF double-submit middleware.

See `nextballup_api.security.csrf` for the full threat model. The middleware
is deliberately narrow: it only blocks requests that are both (a) mutating
and (b) cookie-authenticated. Bearer-header requests, GET/HEAD/OPTIONS, and
the auth bootstrap paths pass through untouched.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from nextballup_api.security.csrf import (
    CSRF_HEADER,
    extract_csrf_cookie,
    path_is_csrf_exempt,
    request_is_cookie_authenticated,
    verify_csrf_token,
)
from nextballup_core.constants import ErrorCode
from nextballup_core.settings import Settings, get_settings


def _reject(request: Request, *, reason: str) -> Response:
    request_id = getattr(request.state, "request_id", None)
    payload: dict[str, object] = {
        "error": {
            "code": ErrorCode.CSRF_FAILED,
            "message": "CSRF verification failed",
            "details": {"reason": reason},
        }
    }
    if isinstance(request_id, str):
        payload["request_id"] = request_id
    # Use JSONResponse directly rather than raising so the CSRF rejection is
    # visible in logs as a 403 with a stable code, matching the app-wide
    # error envelope.
    return JSONResponse(status_code=403, content=payload)


class CsrfMiddleware(BaseHTTPMiddleware):
    """Enforces `X-CSRF-Token == csrf_cookie` on cookie-authenticated mutations.

    Designed to be cheap: only cookie-authenticated requests reach the HMAC
    verify path, and Bearer-authenticated requests short-circuit immediately.
    """

    def __init__(self, app: object, *, settings: Settings | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        # Settings are resolved lazily per-request to cooperate with tests
        # that reload settings after fixture setup.
        self._settings_override = settings

    def _settings(self) -> Settings:
        return self._settings_override or get_settings()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = self._settings()
        method = request.method.upper()
        if method not in {m.upper() for m in settings.csrf_protected_methods}:
            return await call_next(request)

        if path_is_csrf_exempt(request.url.path, settings=settings):
            return await call_next(request)

        if not request_is_cookie_authenticated(request, settings=settings):
            # Bearer-authenticated clients do not need CSRF — they can't be
            # forged cross-origin without the attacker already having the
            # token, in which case CSRF is the least of our worries.
            return await call_next(request)

        cookie_token = extract_csrf_cookie(request, settings=settings)
        header_token = request.headers.get(CSRF_HEADER)
        if not cookie_token or not header_token:
            return _reject(request, reason="missing_token")
        # Double-submit: the two values must match *and* the token must be a
        # valid HMAC of a fresh-enough issue time.
        if cookie_token != header_token:
            return _reject(request, reason="token_mismatch")
        if not verify_csrf_token(cookie_token, settings=settings):
            return _reject(request, reason="invalid_token")

        return await call_next(request)


# Tests use this helper to build a valid header set for cookie-auth paths.
def build_csrf_headers(token: str) -> dict[str, str]:
    return {CSRF_HEADER: token}
