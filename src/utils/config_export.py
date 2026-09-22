"""Redaction pass for GET /system/config-export: strips secrets from the
settings/feeds/system document so the JSON is safe to attach to a bug report.
"""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from utils.http import safe_url_for_log

_REDACT_KEY_NAMES = frozenset((
    'feedauthkey', 'apikey', 'secret', 'password', 'passphrase', 'token', 'key',
))
_STRIP_QUERY_PARAMS = frozenset(('key', 'token', 'auth', 'api_key', 'apikey'))
# Fields present on a webhook dict but not on other URL-carrying entries
# (feeds, source URLs), used to tell "this dict's url is a webhook target".
_WEBHOOK_MARKER_KEYS = frozenset(('events', 'contenttype', 'payloadtemplate'))


def _normalize_key(key: str) -> str:
    return key.replace('_', '').lower()


def _is_webhook_entry(entry: dict) -> bool:
    return 'url' in entry and any(
        _normalize_key(k) in _WEBHOOK_MARKER_KEYS for k in entry)


def _strip_credential_query(url: str) -> str:
    """Drop credential-shaped query params and userinfo; keep scheme/host/path."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    netloc = parts.hostname or ''
    if parts.port:
        netloc = f'{netloc}:{parts.port}'
    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _STRIP_QUERY_PARAMS
    ])
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


def _redact_string(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme in ('http', 'https') and parts.netloc:
        return _strip_credential_query(value)
    return value


def redact_config(value):
    """Recursively strip secret-shaped keys and credential-shaped URL parts."""
    if isinstance(value, dict):
        webhook = _is_webhook_entry(value)
        out = {}
        for k, v in value.items():
            if isinstance(v, bool):
                out[k] = v
                continue
            if _normalize_key(k) in _REDACT_KEY_NAMES:
                continue
            if webhook and k == 'url' and isinstance(v, str):
                out[k] = safe_url_for_log(v)
                continue
            out[k] = redact_config(v)
        return out
    if isinstance(value, list):
        return [redact_config(v) for v in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value
