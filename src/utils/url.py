"""SSRF protection: URL validation for outbound requests.

Validates URLs before they are fetched to prevent Server-Side Request Forgery.
Blocks private/reserved IPs, restricted schemes, and cloud metadata endpoints.
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

from utils.constants import ALLOWED_URL_SCHEMES, ALLOWED_URL_PORTS

logger = logging.getLogger(__name__)

# Cloud metadata IPs that must always be blocked
_CLOUD_METADATA_IPS = frozenset({
    '169.254.169.254',  # AWS, GCP metadata
    '168.63.129.16',    # Azure metadata
})


class SSRFError(ValueError):
    """Raised when a URL fails SSRF validation."""
    pass


def check_resolved_ip(ip_str: str, *, allow_private: bool) -> None:
    """Per-IP SSRF policy shared by pre-validation and the pinned connect.

    Cloud metadata and link-local addresses are blocked at every tier;
    loopback/private/multicast/reserved are allowed only when allow_private.
    """
    if ip_str in _CLOUD_METADATA_IPS:
        raise SSRFError(f"Blocked cloud metadata IP: {ip_str}")
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        raise SSRFError(f"Invalid resolved IP: {ip_str}") from None
    if addr.is_link_local:
        raise SSRFError(f"Blocked link-local IP: {ip_str}")
    if allow_private:
        return
    if addr.is_loopback:
        raise SSRFError(f"Blocked loopback IP: {ip_str}")
    if addr.is_multicast:
        raise SSRFError(f"Blocked multicast IP: {ip_str}")
    if addr.is_private:
        raise SSRFError(f"Blocked private IP: {ip_str}")
    if addr.is_reserved:
        raise SSRFError(f"Blocked reserved IP: {ip_str}")


def validate_url(url: str) -> str:
    """Validate a URL for safe outbound requests.

    Checks scheme, hostname, port, and resolved IP addresses against
    blocklists to prevent SSRF attacks.

    Args:
        url: The URL to validate.

    Returns:
        The validated URL string (stripped).

    Raises:
        SSRFError: If the URL fails any validation check.
    """
    if not url or not url.strip():
        raise SSRFError("Empty URL")

    url = url.strip()
    parsed = urlparse(url)

    # Scheme check
    scheme = (parsed.scheme or '').lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise SSRFError(f"Blocked URL scheme: {scheme!r}")

    # Hostname check
    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("Missing hostname in URL")

    # Port check
    port = parsed.port
    if port is None:
        port = 443 if scheme == 'https' else 80
    if ALLOWED_URL_PORTS and port not in ALLOWED_URL_PORTS:
        raise SSRFError(f"Blocked port: {port}")

    # Resolve hostname and check all IPs. The DNS-rebinding TOCTOU between
    # this check and connect time is closed by utils.pinned_transport, which
    # re-runs check_resolved_ip on the address it actually connects to.
    try:
        addrinfos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise SSRFError(f"Cannot resolve hostname: {hostname!r}") from None

    if not addrinfos:
        raise SSRFError(f"No addresses found for hostname: {hostname!r}")

    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        check_resolved_ip(sockaddr[0], allow_private=False)

    return url


def validate_base_url(url: str) -> str:
    """Validate an operator-configured outbound URL (scheme + hostname + metadata check).

    Unlike validate_url(), this does NOT block private/loopback IPs because
    operator-typed targets (LLM providers, Whisper API, webhook destinations
    like a self-hosted Home Assistant) commonly point to localhost,
    Docker-internal hosts, or LAN IPs on non-default ports. Cloud-provider
    metadata IPs are still blocked so an operator cannot accidentally pivot
    a write into an EC2 IMDS fetch.

    Args:
        url: The URL to validate.

    Returns:
        The validated URL string (stripped).

    Raises:
        SSRFError: If the URL has an invalid scheme, missing hostname, or
            hostname is a literal cloud metadata IP.
    """
    if not url or not url.strip():
        raise SSRFError("Empty URL")

    url = url.strip()
    parsed = urlparse(url)

    scheme = (parsed.scheme or '').lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        raise SSRFError(f"Blocked URL scheme: {scheme!r}")

    if not parsed.hostname:
        raise SSRFError("Missing hostname in URL")

    host = parsed.hostname
    port = parsed.port
    if port is None:
        port = 443 if scheme == 'https' else 80
    validate_outbound_host(host, port)

    return url


def validate_outbound_host(host: str, port: int = 0) -> str:
    """Validate a bare operator-configured hostname (no URL scheme).

    Same policy as validate_base_url: resolve and reject cloud-metadata and
    link-local targets even when reached via a hostname, a DNS rebind, or a
    decimal/IPv6-encoded address. Private and loopback IPs stay allowed
    because operators legitimately point SMTP relays and similar services at
    localhost / Docker / LAN hosts. Unresolvable hosts are allowed: the
    connection fails at connect time and reaches no internal IP.

    Returns the stripped host. Raises SSRFError on a blocked target.
    """
    if not host or not host.strip():
        raise SSRFError("Empty host")
    host = host.strip().strip('[]')
    if host in _CLOUD_METADATA_IPS:
        raise SSRFError(f"Blocked cloud metadata IP: {host}")
    try:
        addrinfos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return host
    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        check_resolved_ip(sockaddr[0], allow_private=True)

    return host
