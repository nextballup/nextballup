from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nextballup_api import __version__
from nextballup_api.errors import register_exception_handlers
from nextballup_api.middleware.request_id import RequestIDMiddleware, current_request_id
from nextballup_api.routers import auth as auth_router
from nextballup_api.routers import games as games_router
from nextballup_api.routers import health as health_router
from nextballup_api.routers import teams as teams_router
from nextballup_api.routers import videos as videos_router
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
    formatter = JsonLogFormatter()
    if not root.handlers:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)
        return
    for existing_handler in root.handlers:
        existing_handler.setFormatter(formatter)


def _validate_startup_secrets() -> None:
    settings = get_settings()
    settings.load_jwt_private_key()
    settings.load_jwt_public_key()


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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    register_exception_handlers(app)

    # Health endpoints are unprefixed (k8s probes expect /health) AND mirrored
    # under /api/v1/health for clients that hit the API surface uniformly.
    app.include_router(health_router.router)
    app.include_router(health_router.router, prefix=API_PREFIX)
    app.include_router(auth_router.router, prefix=API_PREFIX)
    app.include_router(teams_router.router, prefix=API_PREFIX)
    app.include_router(games_router.router, prefix=API_PREFIX)
    app.include_router(videos_router.router, prefix=API_PREFIX)

    return app


app = create_app()
