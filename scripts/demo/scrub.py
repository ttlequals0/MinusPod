import hashlib
import hmac
import ipaddress
import re
from datetime import datetime, timedelta
from urllib.parse import urlparse

PRIVATE_FEED_HOSTS = (
    'patreon.com', 'supercast.com', 'supportingcast.fm', 'memberful.com',
    'glow.fm', 'apple.co',
)
QUERY_ONLY_HOSTS = ('feeds.acast.com', 'substack.com')

_TOKEN_SEGMENT = re.compile(r'[A-Za-z0-9_-]{20,}')
_ISO_TS = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?')
_ISO_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_DOC_V4 = ('192.0.2.', '198.51.100.', '203.0.113.')


def _host_matches(host: str, listed: str) -> bool:
    return host == listed or host.endswith('.' + listed)


def scrub_source_url(url: str, slug: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    private = any(_host_matches(host, h) for h in PRIVATE_FEED_HOSTS)
    private = private or (bool(parsed.query) and any(_host_matches(host, h) for h in QUERY_ONLY_HOSTS))
    tokenised = bool(parsed.query) or any(_TOKEN_SEGMENT.fullmatch(s) for s in parsed.path.split('/'))
    if private or tokenised:
        return f'https://example.com/feeds/{slug}.xml'
    return url


def map_ip(ip: str, salt: bytes) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return '203.0.113.0'
    if addr.is_private or addr.is_loopback or addr.is_link_local:
        return ip
    digest = hmac.new(salt, ip.encode('utf-8'), hashlib.sha256).digest()
    if addr.version == 4:
        slot = int.from_bytes(digest[:4], 'big') % 768
        return f'{_DOC_V4[slot // 256]}{slot % 256}'
    low = int.from_bytes(digest[4:16], 'big')
    return str(ipaddress.IPv6Address((0x20010db8 << 96) | low))


def shift_iso(value: str, delta_days: int) -> str:
    delta = timedelta(days=delta_days)
    if _ISO_DATE.fullmatch(value):
        return (datetime.strptime(value, '%Y-%m-%d') + delta).strftime('%Y-%m-%d')
    if _ISO_TS.fullmatch(value):
        z = value.endswith('Z')
        shifted = datetime.strptime(value.rstrip('Z'), '%Y-%m-%dT%H:%M:%S') + delta
        return shifted.strftime('%Y-%m-%dT%H:%M:%S') + ('Z' if z else '')
    return value


def shift_iso_in_text(text: str, delta_days: int) -> str:
    return _ISO_TS.sub(lambda m: shift_iso(m.group(0), delta_days), text)


def replace_hosts(text: str, hosts: list[str], replacement: str = 'demo.minuspod.app') -> str:
    for host in hosts:
        pattern = re.compile(r'(?<![\w-])' + re.escape(host) + r'''(?=[/:?"'\s]|$)''', re.IGNORECASE)
        text = pattern.sub(replacement, text)
    return text
