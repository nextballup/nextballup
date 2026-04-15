from __future__ import annotations

from fastapi import Request

from nextballup_core.settings import Settings


def client_ip(request: Request, *, settings: Settings) -> str | None:
    peer_ip = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if peer_ip and peer_ip in settings.trusted_proxy_ips and forwarded:
        return forwarded.split(",")[0].strip()
    return peer_ip
