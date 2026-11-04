"""Resolve client identity only through explicitly trusted reverse proxies."""
import ipaddress

from flask import request


def client_address(trusted_proxies: tuple[str, ...]) -> str:
    address = request.remote_addr or "unknown"
    try:
        peer = ipaddress.ip_address(address)
    except ValueError:
        return address
    if any(peer in ipaddress.ip_network(network) for network in trusted_proxies):
        # Caddy replaces this header with one authenticated address. Reject chains.
        forwarded = request.headers.get("X-Forwarded-For", "").strip()
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return str(peer)
