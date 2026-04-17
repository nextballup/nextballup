from __future__ import annotations

from fastapi import Response

from nextballup_core.settings import Settings


def _wire_name(base_name: str, *, settings: Settings) -> str:
    """Apply `__Host-` when the deployment supports it.

    `__Host-` requires Secure + Path=/ + no Domain. Setting it while the
    response is served over plain HTTP would lock the cookie out, so we
    only activate it when `cookie_secure` is enabled — which is the regime
    where the extra isolation actually provides a benefit.
    """
    if settings.cookie_host_prefix and settings.cookie_secure:
        return f"__Host-{base_name}"
    return base_name


def _set_one(
    response: Response,
    *,
    name: str,
    value: str,
    max_age: int,
    path: str,
    settings: Settings,
) -> None:
    use_host_prefix = name.startswith("__Host-")
    response.set_cookie(
        key=name,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        # __Host- forbids Domain; pass None explicitly so a stale env var
        # can't accidentally downgrade the cookie to a plain scope.
        domain=None if use_host_prefix else settings.cookie_domain,
        path=path,
    )


def _clear_refresh_cookie_legacy_paths(response: Response, *, settings: Settings) -> None:
    """Remove every refresh-cookie variant we may have emitted historically.

    Earlier hardening passes wrote the refresh cookie at Path=/ and sometimes
    with a `__Host-` prefix. The refresh cookie is now intentionally narrower
    (Path=`settings.cookie_refresh_path`) so it does not ride on unrelated
    requests. Clearing the legacy variants prevents a stale broad-scoped cookie
    from shadowing the new narrow one during rollout.
    """
    for path in ("/", settings.cookie_refresh_path):
        response.delete_cookie(
            settings.cookie_refresh_name, domain=settings.cookie_domain, path=path
        )
        response.delete_cookie(f"__Host-{settings.cookie_refresh_name}", domain=None, path=path)


def set_auth_cookies(
    response: Response,
    *,
    access_token: str,
    refresh_token: str,
    settings: Settings,
) -> None:
    _set_one(
        response,
        name=_wire_name(settings.cookie_access_name, settings=settings),
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
        settings=settings,
    )
    # The refresh cookie is intentionally *not* `__Host-`-prefixed because the
    # prefix requires Path=/, which would force the refresh JWT onto every API
    # request. Keep it narrower: Path=/api/v1/auth/refresh only.
    _clear_refresh_cookie_legacy_paths(response, settings=settings)
    _set_one(
        response,
        name=settings.cookie_refresh_name,
        value=refresh_token,
        max_age=settings.refresh_token_expire_days * 86400,
        path=settings.cookie_refresh_path,
        settings=settings,
    )


def clear_auth_cookies(response: Response, *, settings: Settings) -> None:
    # Clear both the raw and prefixed forms so a deployment that toggles
    # cookie_host_prefix at runtime doesn't leave stranded cookies behind.
    for base in (settings.cookie_access_name, settings.cookie_refresh_name):
        response.delete_cookie(base, domain=settings.cookie_domain, path="/")
        response.delete_cookie(f"__Host-{base}", domain=None, path="/")
    _clear_refresh_cookie_legacy_paths(response, settings=settings)
