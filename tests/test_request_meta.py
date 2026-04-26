from __future__ import annotations

import pytest
from nextballup_api.request_meta import client_ip
from pydantic import ValidationError
from starlette.requests import Request

from nextballup_core.settings import Settings


def _request(*, peer: str, forwarded_for: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": headers,
            "client": (peer, 443),
        }
    )


def test_client_ip_ignores_forwarded_for_from_untrusted_peer() -> None:
    settings = Settings.model_construct(trusted_proxy_ips=["10.0.0.10"])
    request = _request(peer="198.51.100.10", forwarded_for="203.0.113.9")

    assert client_ip(request, settings=settings) == "198.51.100.10"


def test_client_ip_uses_rightmost_untrusted_hop_from_trusted_proxy_chain() -> None:
    settings = Settings.model_construct(trusted_proxy_ips=["10.0.0.10", "10.0.0.20"])
    request = _request(
        peer="10.0.0.10",
        forwarded_for="203.0.113.9, 10.0.0.20",
    )

    assert client_ip(request, settings=settings) == "203.0.113.9"


def test_client_ip_trusts_configured_proxy_network() -> None:
    settings = Settings.model_construct(trusted_proxy_ips=["10.0.0.0/8"])
    request = _request(
        peer="10.0.0.42",
        forwarded_for="203.0.113.9, 10.0.0.41",
    )
    assert client_ip(request, settings=settings) == "203.0.113.9"


def test_client_ip_trusts_ipv6_proxy_network() -> None:
    settings = Settings.model_construct(trusted_proxy_ips=["2001:db8::/32"])
    request = _request(
        peer="2001:db8::10",
        forwarded_for="2001:db9::5, 2001:db8::11",
    )
    assert client_ip(request, settings=settings) == "2001:db9::5"


def test_client_ip_falls_back_to_peer_on_malformed_forwarded_chain() -> None:
    settings = Settings.model_construct(trusted_proxy_ips=["10.0.0.10"])
    request = _request(peer="10.0.0.10", forwarded_for="not-an-ip, 203.0.113.9")

    assert client_ip(request, settings=settings) == "10.0.0.10"


def test_settings_validates_trusted_proxy_ips() -> None:
    settings = Settings.model_validate({"trusted_proxy_ips": "10.0.0.10, 2001:db8::1"})
    assert settings.trusted_proxy_ips == ["10.0.0.10", "2001:db8::1"]
    assert Settings.model_validate({"trusted_proxy_ips": "[]"}).trusted_proxy_ips == []
    with pytest.raises(ValidationError):
        Settings.model_validate({"trusted_proxy_ips": "not-an-ip"})
