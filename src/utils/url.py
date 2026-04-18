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

    # Resolve hostname and check all IPs.
    # Known residual risk: DNS-rebinding TOCTOU between validation and
    # connect. Closing it requires a custom requests HTTPAdapter that
    # pins the resolved IP (rewriting the URL to the IP while preserving
    # SNI via the Host header). Not implemented; tracked as a follow-up
    # issue titled "Pin resolved IP for SSRF TOCTOU closure".
    try:
        addrinfos = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise SSRFError(f"Cannot resolve hostname: {hostname!r}")

    if not addrinfos:
        raise SSRFError(f"No addresses found for hostname: {hostname!r}")

    for family, _type, _proto, _canonname, sockaddr in addrinfos:
        ip_str = sockaddr[0]

        # Explicit cloud metadata block
        if ip_str in _CLOUD_METADATA_IPS:
            raise SSRFError(f"Blocked cloud metadata IP: {ip_str}")

        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            raise SSRFError(f"Invalid resolved IP: {ip_str}")

        if addr.is_loopback:
            raise SSRFError(f"Blocked loopback IP: {ip_str}")
        if addr.is_link_local:
            raise SSRFError(f"Blocked link-local IP: {ip_str}")
        if addr.is_multicast:
            raise SSRFError(f"Blocked multicast IP: {ip_str}")
        if addr.is_private:
            raise SSRFError(f"Blocked private IP: {ip_str}")
        if addr.is_reserved:
            raise SSRFError(f"Blocked reserved IP: {ip_str}")

    logger.debug(f"URL passed SSRF validation: {url}")
    return url


def validate_base_url(url: str) -> str:
    """Validate a backend service base URL (scheme + hostname + metadata check).

    Unlike validate_url(), this does NOT block private/loopback IPs because
    backend URLs (LLM providers, Whisper API) commonly point to localhost or
    Docker-internal hosts. Cloud-provider metadata IPs are still blocked so
    an operator cannot accidentally pivot a provider write into an EC2 IMDS
    fetch.

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

    host = parsed.hostname.strip('[]')
    if host in _CLOUD_METADATA_IPS:
        raise SSRFError(f"Blocked cloud metadata IP: {host}")

    return url
