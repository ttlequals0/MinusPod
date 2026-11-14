"""SSRF protection: URL validation for outbound requests.

Validates URLs before fetch to prevent SSRF: blocks private/reserved IPs,
restricted schemes and cloud metadata endpoints.
"""
import ipaddress
import logging
import re
import socket
from urllib.parse import urlparse, urlsplit

from utils.constants import ALLOWED_URL_SCHEMES, ALLOWED_URL_PORTS

logger = logging.getLogger(__name__)

# Cloud metadata IPs that must always be blocked
_CLOUD_METADATA_IPS = frozenset({
    ipaddress.ip_address('169.254.169.254'),  # AWS, GCP metadata
    ipaddress.ip_address('168.63.129.16'),    # Azure metadata
})


class SSRFError(ValueError):
    """Raised when a URL fails SSRF validation."""
    pass


# An operator base URL is copied into the non-secret per-run route snapshot
# and returned by GET /settings, so credentials embedded in it would leak.
BASE_URL_USERINFO_ERROR = (
    'Base URL must not embed credentials (user:pass@host). '
    'Put the key in the API key field instead.'
)


def url_has_userinfo(url) -> bool:
    """True when a URL embeds userinfo credentials."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return bool(parts.username or parts.password)


def check_resolved_ip(ip_str: str, *, allow_private: bool) -> None:
    """Per-IP SSRF policy shared by pre-validation and the pinned connect.

    Cloud metadata and link-local addresses are blocked at every tier;
    loopback/private/multicast/reserved are allowed only when allow_private.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        raise SSRFError(f"Invalid resolved IP: {ip_str}") from None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr in _CLOUD_METADATA_IPS:
        raise SSRFError(f"Blocked cloud metadata IP: {ip_str}")
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


def resolve_and_check(host: str, port: int, *, allow_private: bool) -> list[tuple]:
    """Resolve ``host``:``port`` and run check_resolved_ip on every address.

    Returns the addrinfo list. socket.gaierror propagates (an empty result is
    raised as gaierror too) so callers handle unresolvable hosts their own way.
    """
    infos = socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
    if not infos:
        raise socket.gaierror(
            socket.EAI_NONAME, f"No addresses found for hostname: {host!r}")
    for _family, _type, _proto, _canonname, sockaddr in infos:
        check_resolved_ip(sockaddr[0], allow_private=allow_private)
    return infos


# Same set as str.isspace() or category Cc, checked over every code point
URL_SPACE_OR_CONTROL_RE = re.compile(r'[\s\x00-\x1f\x7f-\x9f]')


def reject_url_whitespace(url: str) -> None:
    """Refuse a URL holding whitespace or a control character; requests would repair and use it."""
    if URL_SPACE_OR_CONTROL_RE.search(url):
        raise SSRFError("URL contains whitespace or control characters")


def validate_url(url: str) -> str:
    """Validate a URL for safe outbound requests.

    Checks scheme, hostname, port and resolved IPs against blocklists.

    Returns the stripped URL; raises SSRFError on any failed check.
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
        resolve_and_check(hostname, port, allow_private=False)
    except socket.gaierror:
        raise SSRFError(f"Cannot resolve hostname: {hostname!r}") from None

    return url


def validate_base_url(url: str) -> str:
    """Validate an operator-configured outbound URL (scheme + hostname + metadata check).

    Unlike validate_url(), private/loopback IPs are allowed because operator
    targets (LLM providers, Whisper API, webhooks) commonly sit on localhost,
    Docker or LAN hosts. Cloud metadata IPs stay blocked.

    Returns the stripped URL; raises SSRFError on a bad scheme, missing hostname
    or a literal metadata IP.
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
    link-local targets even via hostname, DNS rebind or decimal/IPv6 encoding.
    Private and loopback IPs stay allowed (SMTP relays on LAN hosts).
    Unresolvable hosts are allowed: the connect fails and reaches no internal IP.

    Returns the stripped host. Raises SSRFError on a blocked target.
    """
    if not host or not host.strip():
        raise SSRFError("Empty host")
    host = host.strip().strip('[]')
    try:
        resolve_and_check(host, port, allow_private=True)
    except socket.gaierror:
        # Unresolvable host reaches no internal IP, so it stays allowed.
        pass

    return host
