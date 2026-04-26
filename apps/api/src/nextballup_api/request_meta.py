from __future__ import annotations

from ipaddress import ip_address, ip_network
from typing import TYPE_CHECKING

from fastapi import Request

from nextballup_core.settings import Settings

if TYPE_CHECKING:
    from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network


def _normalized_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def _parsed_ip(value: str | None) -> IPv4Address | IPv6Address | None:
    if not value:
        return None
    try:
        return ip_address(value.strip())
    except ValueError:
        return None


def _trusted_proxy_matchers(
    values: list[str],
) -> tuple[set[IPv4Address | IPv6Address], tuple[IPv4Network | IPv6Network, ...]]:
    addresses: set[IPv4Address | IPv6Address] = set()
    networks: list[IPv4Network | IPv6Network] = []
    for value in values:
        try:
            addresses.add(ip_address(value))
            continue
        except ValueError:
            pass
        try:
            networks.append(ip_network(value, strict=False))
        except ValueError:
            continue
    return addresses, tuple(networks)


def _is_trusted_proxy(
    value: IPv4Address | IPv6Address,
    *,
    addresses: set[IPv4Address | IPv6Address],
    networks: tuple[IPv4Network | IPv6Network, ...],
) -> bool:
    if value in addresses:
        return True
    return any(value in network for network in networks)


def client_ip(request: Request, *, settings: Settings) -> str | None:
    peer = _parsed_ip(request.client.host if request.client else None)
    peer_ip = str(peer) if peer is not None else None
    forwarded = request.headers.get("x-forwarded-for")
    trusted_addresses, trusted_networks = _trusted_proxy_matchers(settings.trusted_proxy_ips)
    if (
        peer is not None
        and _is_trusted_proxy(
            peer,
            addresses=trusted_addresses,
            networks=trusted_networks,
        )
        and forwarded
    ):
        forwarded_chain: list[IPv4Address | IPv6Address] = []
        for raw_hop in forwarded.split(","):
            hop = _parsed_ip(raw_hop)
            if hop is None:
                return peer_ip
            forwarded_chain.append(hop)
        # X-Forwarded-For is ordered client, proxy1, proxy2. Walk from the
        # trusted peer back toward the client and return the first untrusted
        # hop, which is the best client IP candidate for rate limits/audit.
        for hop in reversed([*forwarded_chain, peer]):
            if not _is_trusted_proxy(
                hop,
                addresses=trusted_addresses,
                networks=trusted_networks,
            ):
                return str(hop)
    return peer_ip
