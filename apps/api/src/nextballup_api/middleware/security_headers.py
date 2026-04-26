"""Attach a conservative set of browser-security headers on every response.

The Next.js frontend already sets CSP / X-Frame-Options / Permissions-Policy
for its own origin (see `apps/web/next.config.ts`). This middleware mirrors
them for direct hits against the API — useful for any client that reaches
the backend without going through the rewrite proxy (mobile webviews,
operator tools pointed at the bare origin, etc.).
"""

from __future__ import annotations

import json

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from nextballup_core.settings import Settings, get_settings


def _hsts_value() -> str:
    # 180 days + preload-eligible values. `includeSubDomains` is safe because
    # the API is never served on a parent domain shared with unaware sites.
    return "max-age=15552000; includeSubDomains"


def _report_to_value(request: Request) -> str:
    endpoint = str(request.url.replace(path="/api/v1/_csp-report", query=""))
    return json.dumps(
        {
            "group": "csp-endpoint",
            "max_age": 10886400,
            "endpoints": [{"url": endpoint}],
        },
        separators=(",", ":"),
    )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: object, *, settings: Settings | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._settings_override = settings

    def _settings(self) -> Settings:
        return self._settings_override or get_settings()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        settings = self._settings()

        # Always-on: safe even for localhost HTTP.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
        )
        # Deny everything the API should never serve — it's a JSON API, not a
        # document origin, so a tight CSP is essentially free insurance.
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
            "report-uri /api/v1/_csp-report; report-to csp-endpoint",
        )
        response.headers.setdefault("Report-To", _report_to_value(request))
        # Cross-origin isolation for the API origin.
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")

        # HSTS only when the deployment has declared itself secure. Setting
        # HSTS under HTTP in dev would pin localhost into HTTPS and wedge
        # every future dev session.
        if settings.cookie_secure or settings.app_env in ("staging", "production"):
            response.headers.setdefault("Strict-Transport-Security", _hsts_value())

        return response
