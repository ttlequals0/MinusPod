"""Apply forwarded headers only when the socket peer is a trusted proxy."""
import ipaddress
import logging

from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger('podcast.proxy')

DEFAULT_TRUSTED_NETWORKS = (
    '127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12',
    '192.168.0.0/16', '100.64.0.0/10', 'fc00::/7',
)

DIRECT_PEER_KEY = 'minuspod.direct_peer'


def direct_peer(environ):
    """Socket peer address, unaffected by forwarded headers."""
    return environ.get(DIRECT_PEER_KEY) or environ.get('REMOTE_ADDR')


def parse_peer(value):
    """Peer string to an ip address with IPv4-mapped IPv6 unwrapped; None when unparseable."""
    try:
        peer = ipaddress.ip_address(value or '')
    except ValueError:
        return None
    if peer.version == 6 and peer.ipv4_mapped:
        return peer.ipv4_mapped
    return peer


def parse_trusted_networks(raw):
    """Parse a comma-separated IP/CIDR list; empty input yields the default set."""
    entries = [e.strip() for e in (raw or '').split(',') if e.strip()] or list(DEFAULT_TRUSTED_NETWORKS)
    networks = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("Ignoring invalid MINUSPOD_TRUSTED_PROXIES entry")
    return networks


class PeerGatedProxyFix:
    """Route through ProxyFix only when the real peer is inside a trusted network."""

    def __init__(self, wsgi_app, hops, networks):
        self.plain = wsgi_app
        self.fixed = ProxyFix(wsgi_app, x_for=hops, x_proto=hops, x_host=hops)
        self.networks = networks

    def _peer_trusted(self, environ):
        peer = parse_peer(environ.get('REMOTE_ADDR'))
        return peer is not None and any(peer in net for net in self.networks)

    def __call__(self, environ, start_response):
        environ[DIRECT_PEER_KEY] = environ.get('REMOTE_ADDR')
        target = self.fixed if self._peer_trusted(environ) else self.plain
        return target(environ, start_response)
