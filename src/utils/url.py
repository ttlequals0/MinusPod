"""SSRF protection: URL validation for outbound requests.

Validates URLs before fetch to prevent SSRF: blocks private/reserved IPs,
restricted schemes and cloud metadata endpoints.
"""
import ipaddress
import functools
import logging
import os
import re
import socket
import time
from urllib.parse import urlparse, urlsplit

from utils.constants import ALLOWED_URL_SCHEMES, ALLOWED_URL_PORTS

logger = logging.getLogger(__name__)

# Cloud metadata IPs that must always be blocked
_CLOUD_METADATA_IPS = frozenset({
    ipaddress.ip_address('169.254.169.254'),  # AWS, GCP metadata
    ipaddress.ip_address('168.63.129.16'),    # Azure metadata
})


# AWS IPv6 metadata endpoint; a listed hostname may not reach it.
_IPV6_METADATA_IP = ipaddress.ip_address('fd00:ec2::254')


class SSRFError(ValueError):
    """Raised when a URL fails SSRF validation."""
    pass


_INVALID_URL_PREFIXES = ('blocked url scheme', 'blocked port', 'empty', 'missing hostname', 'url contains')


def classify_ssrf(message: str) -> tuple[str, str]:
    """(kind, short detail) for an SSRFError message: dns, insecure_redirect, invalid_url or blocked_address."""
    lowered = message.lower()
    if 'cannot resolve hostname' in lowered:
        return 'dns', 'DNS lookup failed'
    if 'https -> http' in lowered:
        return 'insecure_redirect', 'Redirect to HTTP blocked'
    if lowered.startswith(_INVALID_URL_PREFIXES):
        return 'invalid_url', 'Address not allowed'
    return 'blocked_address', 'Address blocked'


def ssrf_reject_text(exc: Exception, label: str, default: str | None = None) -> str:
    """User-facing 400 text for an SSRFError; a DNS failure is not reported as a rejected address."""
    if classify_ssrf(str(exc))[0] == 'dns':
        return f'Could not resolve the host name in the {label}'
    return default if default is not None else f'Invalid {label}: {exc}'


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


PRIVATE_FEED_HOSTS_ENV = 'MINUSPOD_PRIVATE_FEED_HOSTS'
_HOSTNAME_RE = re.compile(r'^(?=.{1,253}$)[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?(\.[a-z0-9_]([a-z0-9_-]{0,61}[a-z0-9_])?)*$')


def normalize_host(host: str) -> str:
    return host.strip().strip('[]').lower().rstrip('.')


def _is_sensitive_address(addr) -> bool:
    """Loopback, link-local, unspecified and cloud metadata addresses."""
    return (addr.is_loopback or addr.is_link_local or addr.is_unspecified
            or addr in _CLOUD_METADATA_IPS or addr == _IPV6_METADATA_IP)


class PrivateFeedHosts:
    """Feed source hosts allowed on private addresses (MINUSPOD_PRIVATE_FEED_HOSTS)."""

    def __init__(self, hostnames=(), networks=(), host_ports=(), net_ports=()):
        self.hostnames = frozenset(hostnames)
        self.networks = tuple(networks)
        self.host_ports = frozenset(host_ports)
        self.net_ports = tuple(net_ports)

    def permits_port(self, host: str | None, port: int) -> bool:
        """True when a `host:port` or `ip:port` entry names this exact host and port."""
        if host is None:
            return False
        name = normalize_host(host)
        if (name, port) in self.host_ports:
            return True
        try:
            addr = ipaddress.ip_address(name)
        except ValueError:
            return False
        return any(addr in net and port == p for net, p in self.net_ports)

    def __bool__(self):
        return bool(self.hostnames or self.networks)

    def permits(self, host: str | None, addr) -> bool:
        """True when a non-public address is allowed for this connection host."""
        sensitive = _is_sensitive_address(addr)
        for net in self.networks:
            # A range never opens a sensitive address; only an exact single-address entry does.
            if addr in net and (not sensitive or net.num_addresses == 1):
                return True
        if host is None or normalize_host(host) not in self.hostnames:
            return False
        # A listed name must not be re-pointed at loopback, link-local or metadata.
        return not (sensitive or addr.is_multicast or addr.is_reserved)


def _split_host_port(entry: str):
    """(host, port) for `name:port`, `1.2.3.4:port` or `[v6]:port`; None when the entry has no port."""
    if entry.startswith('['):
        host, _, rest = entry[1:].partition(']')
        port = rest[1:] if rest.startswith(':') else ''
    else:
        host, sep, port = entry.rpartition(':')
        if not sep or ':' in host:
            return None
    if not port.isdigit() or not 0 < int(port) < 65536:
        return None
    return host, int(port)


def parse_private_feed_hosts(raw: str) -> PrivateFeedHosts:
    """Parse the allow-list; invalid entries are skipped with a warning."""
    hostnames, networks, host_ports, net_ports = set(), [], set(), []
    for entry in (raw or '').split(','):
        entry = entry.strip()
        if not entry:
            continue
        candidate = entry.strip('[]') if entry.startswith('[') and ']' in entry and '/' not in entry else entry
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
            continue
        except ValueError:
            pass
        port = None
        if '/' not in entry and (split := _split_host_port(entry)):
            entry, port = split
        name = normalize_host(entry)
        try:
            net = ipaddress.ip_network(name, strict=False)
        except ValueError:
            net = None
        if net is not None and port is not None:
            networks.append(net)
            net_ports.append((net, port))
        elif net is None and _HOSTNAME_RE.match(name) and '/' not in entry:
            hostnames.add(name)
            if port is not None:
                host_ports.add((name, port))
        else:
            logger.warning("Ignoring invalid %s entry: %r", PRIVATE_FEED_HOSTS_ENV, entry)
    return PrivateFeedHosts(hostnames, networks, host_ports, net_ports)


@functools.lru_cache(maxsize=8)
def _cached_private_feed_hosts(raw: str) -> PrivateFeedHosts:
    return parse_private_feed_hosts(raw)


def get_private_feed_hosts() -> PrivateFeedHosts:
    """Current allow-list from the environment (parsed once per distinct value)."""
    return _cached_private_feed_hosts(os.environ.get(PRIVATE_FEED_HOSTS_ENV, ''))


def check_resolved_ip(ip_str: str, *, allow_private: bool, host: str | None = None,
                      private_hosts: PrivateFeedHosts | None = None) -> None:
    """Per-IP SSRF policy shared by pre-validation and the pinned connect.

    Cloud metadata and link-local addresses are blocked at every tier;
    loopback/private/multicast/reserved and any non-global address are allowed
    only when allow_private, or when private_hosts permits this host/address.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        raise SSRFError(f"Invalid resolved IP: {ip_str}") from None
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if private_hosts and not allow_private and private_hosts.permits(host, addr):
        return
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
    # Catches ranges is_private misses, e.g. 100.64.0.0/10 shared address space
    if not addr.is_global:
        raise SSRFError(f"Blocked non-global IP: {ip_str}")


def _resolve_with_retry(host: str, port: int, *, attempts: int = 3,
                        base_delay: float = 0.15) -> list[tuple]:
    """getaddrinfo retried briefly on transient EAI_AGAIN (Docker's resolver under load); NXDOMAIN is not."""
    for attempt in range(attempts):
        try:
            return socket.getaddrinfo(host, port or None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            if exc.errno != socket.EAI_AGAIN or attempt == attempts - 1:
                raise
            time.sleep(base_delay * (attempt + 1))


def resolve_and_check(host: str, port: int, *, allow_private: bool,
                      private_hosts: PrivateFeedHosts | None = None) -> list[tuple]:
    """Resolve ``host``:``port`` and run check_resolved_ip on every address.

    Returns the addrinfo list. socket.gaierror propagates (an empty result is
    raised as gaierror too) so callers handle unresolvable hosts their own way.
    """
    infos = _resolve_with_retry(host, port)
    if not infos:
        raise socket.gaierror(
            socket.EAI_NONAME, f"No addresses found for hostname: {host!r}")
    for _family, _type, _proto, _canonname, sockaddr in infos:
        check_resolved_ip(sockaddr[0], allow_private=allow_private,
                          host=host, private_hosts=private_hosts)
    return infos


# Same set as str.isspace() or category Cc, checked over every code point
URL_SPACE_OR_CONTROL_RE = re.compile(r'[\s\x00-\x1f\x7f-\x9f]')


def reject_url_whitespace(url: str) -> None:
    """Refuse a URL holding whitespace or a control character; requests would repair and use it."""
    if URL_SPACE_OR_CONTROL_RE.search(url):
        raise SSRFError("URL contains whitespace or control characters")


def validate_url(url: str, private_hosts: PrivateFeedHosts | None = None) -> str:
    """Validate a URL for safe outbound requests.

    Checks scheme, hostname, port and resolved IPs against blocklists;
    private_hosts exempts listed hosts/addresses (feed sources only).

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
    if (ALLOWED_URL_PORTS and port not in ALLOWED_URL_PORTS
            and not (private_hosts and private_hosts.permits_port(hostname, port))):
        raise SSRFError(f"Blocked port: {port}")

    # Resolve hostname and check all IPs. The DNS-rebinding TOCTOU between
    # this check and connect time is closed by utils.pinned_transport, which
    # re-runs check_resolved_ip on the address it actually connects to.
    try:
        resolve_and_check(hostname, port, allow_private=False, private_hosts=private_hosts)
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
