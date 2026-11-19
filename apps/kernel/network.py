"""Client-address resolution that trusts forwarding headers only from configured proxies."""

import ipaddress
from functools import lru_cache

from django.conf import settings

UNKNOWN = "unknown"


def _parse(value: str):
    """Parse an address token (`ip`, `ip:port`, `[ip]:port`); IPv4-mapped IPv6 becomes IPv4."""
    token = value.strip()
    if token.startswith("["):
        token = token[1:].partition("]")[0]
    elif token.count(":") == 1:
        token = token.partition(":")[0]
    try:
        address = ipaddress.ip_address(token)
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


@lru_cache(maxsize=8)
def _networks(entries: tuple[str, ...]):
    return tuple(ipaddress.ip_network(entry, strict=False) for entry in entries)


def _is_trusted(address, networks) -> bool:
    return any(address.version == network.version and address in network for network in networks)


def client_ip(request) -> str:
    peer = _parse(request.META.get("REMOTE_ADDR", ""))
    if peer is None:
        return UNKNOWN
    networks = _networks(tuple(settings.TRUSTED_PROXIES))
    if not networks or not _is_trusted(peer, networks):
        return str(peer)
    header = request.META.get("HTTP_X_FORWARDED_FOR", "")
    # The right-most entries were appended by our own proxies; the first one that is
    # not a trusted proxy is the client. Anything to its left is attacker-controlled.
    for token in reversed(header.split(",")):
        if not token.strip():
            continue
        address = _parse(token)
        if address is None:
            return str(peer)
        if not _is_trusted(address, networks):
            return str(address)
    return str(peer)
