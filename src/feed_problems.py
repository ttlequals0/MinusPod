"""Plain-language classes for feed refresh failures, and a URL-free detail string."""
import re

import requests

from database.error_text import redact_error
from utils.http import safe_url_for_log
from utils.url import SSRFError

KIND_MESSAGES = {
    'dns': 'The host name could not be found.',
    'tls': 'The site has a security certificate problem.',
    'timeout': 'The site took too long to respond.',
    'connection': 'Could not connect to the site.',
    'gone': 'The feed was not found. It may have moved or been removed.',
    'auth': 'The site wants a login or a private feed link.',
    'rate_limited': 'The site is rate limiting us or is temporarily unavailable.',
    'http_error': 'The site returned an error.',
    'invalid_xml': 'The response is not a valid podcast feed.',
    'blocked_address': 'The address points to a private network, which is blocked.',
    'not_feed': 'The address did not return a podcast feed.',
    'unknown': 'The feed could not be refreshed.',
}

_DNS_MARKERS = ('name or service not known', 'getaddrinfo', 'nameresolutionerror',
                'nodename nor servname', 'temporary failure in name resolution')
_URL_RE = re.compile(r'https?://[^\s\'")>\]]+', re.IGNORECASE)
DETAIL_LIMIT = 300


def kind_for_status(status) -> str:
    if status in (404, 410):
        return 'gone'
    if status in (401, 403):
        return 'auth'
    if status in (429, 503):
        return 'rate_limited'
    return 'http_error'


def classify_exception(exc) -> tuple[str, str]:
    """(kind, short detail) for a fetch exception; the detail never carries a URL."""
    if isinstance(exc, SSRFError):
        return 'blocked_address', 'Address blocked'
    response = getattr(exc, 'response', None)
    status = getattr(response, 'status_code', None)
    if isinstance(exc, requests.exceptions.HTTPError) and status:
        return kind_for_status(status), f'HTTP {status}'
    if isinstance(exc, requests.exceptions.SSLError):
        return 'tls', 'TLS certificate error'
    if isinstance(exc, requests.exceptions.Timeout):
        return 'timeout', 'Timed out'
    if isinstance(exc, requests.exceptions.ConnectionError):
        text = str(exc).lower()
        if any(marker in text for marker in _DNS_MARKERS):
            return 'dns', 'DNS lookup failed'
        return 'connection', 'Connection failed'
    return 'unknown', type(exc).__name__


def classify_text(text) -> str:
    """Best-effort kind for a stored error message that predates the kind column."""
    lowered = (text or '').lower()
    if not lowered:
        return 'unknown'
    if 'could not be parsed' in lowered or 'did not parse' in lowered:
        return 'invalid_xml'
    match = re.search(r'\b(?:http|status)[ :]*(\d{3})\b', lowered)
    if match:
        return kind_for_status(int(match.group(1)))
    if any(marker in lowered for marker in _DNS_MARKERS):
        return 'dns'
    if 'ssl' in lowered or 'certificate' in lowered:
        return 'tls'
    if 'timed out' in lowered or 'timeout' in lowered:
        return 'timeout'
    return 'unknown'


def scrub_detail(text) -> str | None:
    """Stored error text with token redaction and every URL cut down to scheme and host."""
    if not text:
        return None
    cleaned = redact_error(_URL_RE.sub(lambda m: safe_url_for_log(m.group(0)), str(text)))
    return cleaned[:DETAIL_LIMIT]


def fetch_failure_detail(meta) -> str:
    """The stored error text for a failed fetch, with the specific cause when the fetch recorded one."""
    detail = (meta or {}).get('detail')
    if detail:
        return f'Failed to fetch RSS feed: {detail}'
    return 'Failed to fetch RSS feed (unreachable, invalid response, or blocked)'
