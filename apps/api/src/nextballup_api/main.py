from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nextballup_api import __version__
from nextballup_api.billing import get_billing_provider
from nextballup_api.email_delivery import get_email_provider
from nextballup_api.errors import register_exception_handlers
from nextballup_api.middleware.csrf import CsrfMiddleware
from nextballup_api.middleware.request_id import RequestIDMiddleware, current_request_id
from nextballup_api.middleware.security_headers import SecurityHeadersMiddleware
from nextballup_api.routers import admin as admin_router
from nextballup_api.routers import auth as auth_router
from nextballup_api.routers import csp as csp_router
from nextballup_api.routers import games as games_router
from nextballup_api.routers import health as health_router
from nextballup_api.routers import mfa as mfa_router
from nextballup_api.routers import teams as teams_router
from nextballup_api.routers import videos as videos_router
from nextballup_api.security.csrf import CSRF_HEADER
from nextballup_core.constants import REQUEST_ID_HEADER
from nextballup_core.demo_preview import validate_demo_preview_runtime
from nextballup_core.logging import install_log_redaction_filter
from nextballup_core.settings import get_settings
from nextballup_db.engine import dispose_engine

API_PREFIX = "/api/v1"

logger = logging.getLogger("nextballup_api")


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": current_request_id(),
        }
        return json.dumps(payload, default=str)


def _configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    install_log_redaction_filter(root)
    formatter = JsonLogFormatter()
    if not root.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        install_log_redaction_filter(root)
        root.addHandler(stream_handler)
        return
    for existing_handler in root.handlers:
        existing_handler.setFormatter(formatter)
    install_log_redaction_filter(root)


def _validate_startup_secrets() -> None:
    settings = get_settings()
    settings.load_jwt_private_key()
    settings.load_jwt_public_key()
    settings.runtime_database_url()
    # Non-dev deployments MUST set CSRF_SECRET explicitly. The dev fallback
    # (derived from the JWT private key) is fine for local work but should
    # never be trusted in staging or production: a CSRF secret that is a
    # deterministic function of another secret weakens defense-in-depth.
    if settings.app_env in ("staging", "production") and not settings.csrf_secret:
        raise RuntimeError(
            "CSRF_SECRET must be configured in staging/production "
            "(fail-closed; cannot fall back to JWT-derived dev secret)"
        )
    if settings.app_env in ("staging", "production"):
        failures: list[str] = []
        if not settings.cookie_secure:
            failures.append("COOKIE_SECURE must be true in staging/production")
        if settings.cookie_samesite != "strict":
            failures.append("COOKIE_SAMESITE must be 'strict' in staging/production")
        if not settings.cookie_host_prefix:
            failures.append(
                "COOKIE_HOST_PREFIX must be true in staging/production "
                "(required for __Host- cookie isolation)"
            )
        if not settings.redis_url:
            failures.append(
                "REDIS_URL must be configured in staging/production "
                "(fail-closed; auth/upload/team-join abuse controls require Redis)"
            )
        if not settings.mfa_secret_key or len(settings.mfa_secret_key.encode("utf-8")) < 32:
            failures.append(
                "MFA_SECRET_KEY must be configured to at least 32 UTF-8 bytes in staging/production"
            )
        if settings.email_delivery_provider in {"logging", "noop"}:
            failures.append(
                "EMAIL_DELIVERY_PROVIDER must be a real registered provider in staging/production"
            )
        else:
            try:
                get_email_provider(settings)
            except RuntimeError as exc:
                failures.append(str(exc))
        if settings.billing_provider == "stub":
            failures.append("BILLING_PROVIDER must not be 'stub' in staging/production")
        elif settings.app_env == "production" and settings.billing_provider == "billing_disabled":
            failures.append(
                "BILLING_PROVIDER must be a real registered provider in production "
                "(`billing_disabled` is alpha/staging only)"
            )
        else:
            try:
                get_billing_provider(settings)
            except RuntimeError as exc:
                failures.append(str(exc))
        if settings.observability_metrics_enabled and not settings.observability_metrics_token:
            failures.append(
                "OBSERVABILITY_METRICS_TOKEN must be configured when metrics are enabled"
            )
        if settings.cookie_domain is not None:
            failures.append(
                "COOKIE_DOMAIN must be unset in staging/production when using __Host- cookies"
            )
        # docs/soc2/DEPLOYMENT_CHANNELS.md requires alpha/beta to be locked
        # at the edge or invite-only; refuse to boot a non-dev channel that
        # leaves /auth/register open to the public.
        if settings.registration_mode == "open":
            failures.append(
                "REGISTRATION_MODE must not be 'open' in staging/production "
                "(public/alpha/beta channels require invite_only, allowlist, or disabled)"
            )
        if settings.registration_mode == "invite_only" and not settings.registration_invite_codes:
            failures.append(
                "REGISTRATION_INVITE_CODES must be configured when REGISTRATION_MODE='invite_only'"
            )
        if settings.registration_mode == "allowlist" and not settings.registration_email_allowlist:
            failures.append(
                "REGISTRATION_EMAIL_ALLOWLIST must be configured when REGISTRATION_MODE='allowlist'"
            )
        frontend_url = urlparse(settings.frontend_app_url)
        frontend_host = (frontend_url.hostname or "").lower()
        if frontend_url.scheme not in {"http", "https"} or not frontend_url.netloc:
            failures.append("FRONTEND_APP_URL must be an absolute http/https URL")
        elif frontend_host in {"localhost", "127.0.0.1", "::1"}:
            failures.append("FRONTEND_APP_URL must not point at localhost in staging/production")
        if failures:
            raise RuntimeError(" / ".join(failures))
    if settings.cv_demo_preview_enabled and settings.app_env not in ("development", "test"):
        raise RuntimeError(
            "CV_DEMO_PREVIEW_ENABLED is only allowed in development/test "
            "(fail-closed; do not shell out to the sibling training repo in staging/production)"
        )
    validate_demo_preview_runtime(settings, startup=True)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    _validate_startup_secrets()
    logger.info("API starting", extra={"version": __version__})
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("API stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging("DEBUG" if settings.app_debug else "INFO")

    app = FastAPI(
        title="NextBallUp API",
        version=__version__,
        debug=settings.app_debug,
        lifespan=_lifespan,
    )

    # Explicit allowlists instead of "*" — with credentials enabled, a
    # wildcard allow_methods/allow_headers is a footgun (any header the
    # attacker page can set would be echoed back with CORS approval).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Authorization",
            "Content-Type",
            REQUEST_ID_HEADER,
            CSRF_HEADER,
            "If-Match",
            "If-None-Match",
        ],
        expose_headers=[REQUEST_ID_HEADER],
    )
    # Order matters: CSRF runs *after* RequestID (so the request_id is on
    # state when CSRF rejects) but *before* the routers — adding
    # middleware-after-router in Starlette means outermost-added runs first.
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    # Health endpoints are unprefixed (k8s probes expect /health) AND mirrored
    # under /api/v1/health for clients that hit the API surface uniformly.
    app.include_router(health_router.router)
    app.include_router(health_router.router, prefix=API_PREFIX)
    app.include_router(auth_router.router, prefix=API_PREFIX)
    app.include_router(mfa_router.router, prefix=API_PREFIX)
    app.include_router(teams_router.router, prefix=API_PREFIX)
    app.include_router(games_router.router, prefix=API_PREFIX)
    app.include_router(videos_router.router, prefix=API_PREFIX)
    app.include_router(admin_router.router, prefix=API_PREFIX)
    app.include_router(csp_router.router, prefix=API_PREFIX)

    return app


app = create_app()
