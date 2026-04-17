"""Shared CSRF helper for test fixtures that build their own AsyncClient."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from httpx import Request


def make_csrf_mirror_hook() -> Callable[[Request], Awaitable[None]]:
    """Return an httpx event hook that mirrors the CSRF cookie to the header.

    Browsers do this via `apiFetch` on the frontend; tests need the same
    behavior so the CSRF middleware accepts mutating requests.
    """
    from nextballup_api.security.csrf import CSRF_HEADER

    from nextballup_core.settings import get_settings

    settings = get_settings()
    csrf_cookie_names = (
        settings.cookie_csrf_name,
        f"__Host-{settings.cookie_csrf_name}",
    )
    mutating = {"POST", "PUT", "PATCH", "DELETE"}

    async def _hook(request: Request) -> None:
        if request.method not in mutating:
            return
        if CSRF_HEADER in request.headers:
            return
        cookie_header = request.headers.get("cookie", "")
        if not cookie_header:
            return
        for chunk in cookie_header.split(";"):
            name, _, value = chunk.strip().partition("=")
            if name in csrf_cookie_names and value:
                request.headers[CSRF_HEADER] = value
                return

    return _hook
