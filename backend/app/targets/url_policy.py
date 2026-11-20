"""Which URLs may be registered as a target, and therefore receive anything at all.

V1 only allows approved local or private lab addresses
(docs/architecture/PRESENTATION_INTEGRATION_PLAN.md, section 13.1):

1. http or https, no user name or password, no path, query or fragment;
2. the host is an IP address literal, or exactly `localhost` (taken as 127.0.0.1).
   Host names are refused, so DNS cannot point a registered target somewhere
   else later (no DNS rebinding, no DNS-based SSRF);
3. the address is inside TARGET_ALLOWED_CIDRS (default 127.0.0.0/8) and is never
   unspecified, link-local (which includes the cloud metadata address
   169.254.169.254), multicast, reserved or a known metadata address;
4. the port is explicit, inside TARGET_ALLOWED_PORTS (default 8090), and never
   one of CloudAttackLab's own ports.

The check never sends anything. It runs at registration and again before every
request to the target, against the address pinned at registration.
"""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlsplit

# CloudAttackLab's own services (docker-compose.yml): a target can never be one of them.
PLATFORM_PORTS = frozenset({8000, 5173, 5432, 3000, 3100, 12345})

# Metadata services that are not link-local.
METADATA_ADDRESSES = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),  # Alibaba Cloud
        ipaddress.ip_address("fd00:ec2::254"),    # AWS IMDS over IPv6
    }
)


class UrlRefused(Exception):
    """The URL is not an approved lab target. Nothing was sent to it."""


@dataclass(frozen=True)
class ApprovedUrl:
    base_url: str   # rebuilt from the pinned address, e.g. "http://127.0.0.1:8090"
    scheme: str
    ip: str         # the pinned address
    port: int


def check_url(url: str, *, allowed_cidrs: Iterable[str], allowed_ports: Iterable[int]) -> ApprovedUrl:
    """Return the approved, normalized URL, or raise UrlRefused with the reason."""
    if not isinstance(url, str) or not url.strip():
        raise UrlRefused("the URL is empty")
    url = url.strip()
    if len(url) > 300:
        raise UrlRefused("the URL is too long")
    if any(ch.isspace() or not ch.isprintable() for ch in url):
        raise UrlRefused("the URL contains spaces or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UrlRefused(f"it is not a valid URL ({exc})") from exc

    if parts.scheme not in ("http", "https"):
        raise UrlRefused("only http:// and https:// are allowed")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UrlRefused("it must not contain a user name or password")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise UrlRefused("give the application's base URL only, with no path, query or fragment")

    host = (parts.hostname or "").lower()
    if not host:
        raise UrlRefused("it has no host")
    if host == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise UrlRefused(
            f"'{host}' is a host name. V1 accepts only an IP address (for example http://127.0.0.1:8090) "
            "or localhost, so DNS can never redirect the target"
        ) from None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    _refuse_special(address)

    networks = [ipaddress.ip_network(cidr, strict=False) for cidr in allowed_cidrs]
    if not any(address.version == net.version and address in net for net in networks):
        allowed = ", ".join(str(n) for n in networks) or "none"
        raise UrlRefused(f"{address} is not inside the approved lab ranges ({allowed})")

    if port is None:
        raise UrlRefused("give the port explicitly (for example :8090)")
    if port in PLATFORM_PORTS:
        raise UrlRefused(f"port {port} belongs to CloudAttackLab itself")
    ports = set(allowed_ports)
    if port not in ports:
        raise UrlRefused(f"port {port} is not an approved target port ({', '.join(map(str, sorted(ports))) or 'none'})")

    host_part = f"[{address}]" if address.version == 6 else str(address)
    return ApprovedUrl(base_url=f"{parts.scheme}://{host_part}:{port}", scheme=parts.scheme, ip=str(address), port=port)


def _refuse_special(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if address in METADATA_ADDRESSES:
        raise UrlRefused(f"{address} is a cloud metadata address")
    if address.is_link_local:
        raise UrlRefused(f"{address} is link-local (this includes the cloud metadata address 169.254.169.254)")
    if address.is_unspecified:
        raise UrlRefused(f"{address} is the unspecified address")
    if address.is_multicast:
        raise UrlRefused(f"{address} is a multicast address")
    if address.is_reserved:
        raise UrlRefused(f"{address} is a reserved address")
    if isinstance(address, ipaddress.IPv4Address) and address == ipaddress.ip_address("255.255.255.255"):
        raise UrlRefused("the broadcast address is not allowed")


def check_pinned(base_url: str, ip: str, port: int, *, allowed_cidrs, allowed_ports) -> ApprovedUrl:
    """Re-check a stored target before a request: still allowed, and still the pinned address."""
    approved = check_url(base_url, allowed_cidrs=allowed_cidrs, allowed_ports=allowed_ports)
    if approved.ip != ip or approved.port != port or approved.base_url != base_url:
        raise UrlRefused("the stored URL no longer matches the address approved at registration")
    return approved
