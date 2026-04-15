from __future__ import annotations

from fastapi import Response

from nextballup_core.settings import Settings


def _set_one(
    response: Response,
    *,
    name: str,
    value: str,
    max_age: int,
    settings: Settings,
) -> None:
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        domain=settings.cookie_domain,
        path="/",
    )


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    settings: Settings,
) -> None:
    _set_one(
        response,
        name=settings.cookie_access_name,
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        settings=settings,
    )
    _set_one(
        response,
        name=settings.cookie_refresh_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        settings=settings,
    )


def clear_auth_cookies(response: Response, *, settings: Settings) -> None:
    response.delete_cookie(
        settings.cookie_access_name,
        domain=settings.cookie_domain,
        path="/",
    )
    response.delete_cookie(
        settings.cookie_refresh_name,
        domain=settings.cookie_domain,
        path="/",
    )
