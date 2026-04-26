"""Stateless double-submit CSRF tokens.

Threat model: the frontend is same-origin with the API (Next.js rewrite
proxies `/api/v1/*` to the backend). Auth cookies are httpOnly + SameSite=Lax,
so cross-site POSTs *without* user interaction already fail. Double-submit
closes the remaining gap — a same-site subframe or a leaked SameSite=None
cookie deployment would otherwise still accept attacker-forged requests.

The token itself is an HMAC over a random nonce + issue timestamp, so the
server does not need Redis or DB state to verify. The cookie is NOT httpOnly
on purpose: the browser-side `apiFetch` helper reads it and echoes it in the
`X-CSRF-Token` header. An attacker sitting on another origin cannot read the
cookie (same-origin policy), so they cannot forge the header.

Bearer-authenticated requests (`Authorization: Bearer ...`) skip CSRF
entirely — they're not cookie-authenticated, so the classical CSRF attack
vector does not apply. This preserves the API-client auth path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from typing import Final

from fastapi import Request, Response

from nextballup_core.settings import Settings

CSRF_HEADER: Final[str] = "X-CSRF-Token"
_NONCE_BYTES: Final[int] = 16


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(nonce: bytes, issued_at: int, *, secret: str) -> bytes:
    payload = nonce + issued_at.to_bytes(8, "big")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()


def generate_csrf_token(*, settings: Settings) -> str:
    """Mint a new CSRF token. Returned as `<b64nonce>.<b64sig>.<issued_at>`.

    The issued_at is encoded in the token (not the cookie metadata) so we can
    reject tokens older than the configured TTL without any server state.
    """
    nonce = secrets.token_bytes(_NONCE_BYTES)
    issued_at = int(time.time())
    sig = _sign(nonce, issued_at, secret=settings.effective_csrf_secret())
    return f"{_b64url_encode(nonce)}.{_b64url_encode(sig)}.{issued_at}"


def verify_csrf_token(token: str, *, settings: Settings) -> bool:
    if not token or token.count(".") != 2:
        return False
    nonce_b64, sig_b64, issued_at_raw = token.split(".")
    try:
        nonce = _b64url_decode(nonce_b64)
        provided_sig = _b64url_decode(sig_b64)
        issued_at = int(issued_at_raw)
    except (ValueError, UnicodeDecodeError):
        return False
    if len(nonce) != _NONCE_BYTES:
        return False

    age = int(time.time()) - issued_at
    if age < 0 or age > settings.csrf_token_ttl_seconds:
        return False
    expected = _sign(nonce, issued_at, secret=settings.effective_csrf_secret())
    return hmac.compare_digest(expected, provided_sig)


def set_csrf_cookie(response: Response, *, token: str, settings: Settings) -> None:
    """Attach the CSRF cookie. NOT httpOnly — by design — so the same-origin
    frontend can read it and echo it in the `X-CSRF-Token` header."""
    # __Host- prefix requires Secure + Path=/ + no Domain. Only apply when
    # cookie_secure is enabled; setting it under plain HTTP locks the cookie
    # out entirely because Chrome rejects Secure cookies on non-HTTPS origins
    # even on localhost unless the user explicitly flips a flag.
    name = settings.cookie_csrf_name
    if settings.cookie_host_prefix and settings.cookie_secure:
        name = f"__Host-{settings.cookie_csrf_name}"
    response.set_cookie(
        key=name,
        value=token,
        max_age=settings.csrf_token_ttl_seconds,
        httponly=False,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        # __Host- forbids Domain; pass None explicitly.
        domain=None if name.startswith("__Host-") else settings.cookie_domain,
        path="/",
    )


def clear_csrf_cookie(response: Response, *, settings: Settings) -> None:
    name = settings.cookie_csrf_name
    if settings.cookie_host_prefix and settings.cookie_secure:
        name = f"__Host-{settings.cookie_csrf_name}"
    response.delete_cookie(
        name,
        domain=None if name.startswith("__Host-") else settings.cookie_domain,
        path="/",
    )


def extract_csrf_cookie(request: Request, *, settings: Settings) -> str | None:
    prefixed = f"__Host-{settings.cookie_csrf_name}"
    if settings.cookie_host_prefix and settings.cookie_secure:
        return request.cookies.get(prefixed) or request.cookies.get(settings.cookie_csrf_name)
    return request.cookies.get(settings.cookie_csrf_name) or request.cookies.get(prefixed)


def request_is_cookie_authenticated(request: Request, *, settings: Settings) -> bool:
    """True when an access cookie is present on the request.

    Bearer-only API clients still skip CSRF. Cookie + Bearer is deliberately
    treated as cookie-authenticated so a stray Authorization header cannot
    suppress the browser CSRF check while the downstream auth dependency uses
    the cookie.
    """
    access_cookie = request.cookies.get(settings.cookie_access_name)
    prefixed = request.cookies.get(f"__Host-{settings.cookie_access_name}")
    return bool(access_cookie or prefixed)


def path_is_csrf_exempt(path: str, *, settings: Settings) -> bool:
    return any(path == exempt or path.startswith(exempt) for exempt in settings.csrf_exempt_paths)
