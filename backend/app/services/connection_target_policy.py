"""Deployment-controlled egress policy for customer database targets."""

from __future__ import annotations

import ipaddress
import os


def connection_target_allowed(host: str) -> bool:
    """Allow localhost for development, otherwise require an explicit host/CIDR.

    DNS names are compared as names; production networking must additionally
    enforce the same allowlist at the egress firewall.
    """
    candidate = (host or "").strip().lower().rstrip(".")
    if candidate in {"localhost", "127.0.0.1", "::1"}:
        return True
    configured = [x.strip().lower() for x in os.getenv("DATAPILOT_DB_ALLOWED_HOSTS", "").split(",") if x.strip()]
    if not configured:
        return False
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return candidate in configured
    for item in configured:
        try:
            if address in ipaddress.ip_network(item, strict=False):
                return True
        except ValueError:
            if candidate == item:
                return True
    return False
