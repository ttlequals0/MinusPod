"""Redaction pass for GET /system/config-export: strips secrets from the
settings/feeds/system document so the JSON is safe to attach to a bug report.
"""
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from utils.http import safe_url_for_log

_REDACT_KEY_SUBSTRINGS = ('secret', 'password', 'passphrase', 'token', 'apikey')
_STRIP_QUERY_PARAMS = frozenset(('key', 'token', 'auth', 'api_key', 'apikey'))
_EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
# Fields present on a webhook dict but not on other URL-carrying entries
# (feeds, source URLs), used to tell "this dict's url is a webhook target".
_WEBHOOK_MARKER_KEYS = frozenset(('events', 'contenttype', 'payloadtemplate'))
# Well-known public LLM/transcription/podcast-index providers, kept visible
# in the settings section instead of being masked as <private-host>.
_PUBLIC_PROVIDER_HOSTS = frozenset((
    'api.openai.com', 'api.anthropic.com', 'openrouter.ai', 'api.groq.com',
    'api.together.xyz', 'api.deepseek.com', 'api.mistral.ai', 'api.x.ai',
    'api.perplexity.ai', 'api.fireworks.ai', 'api.cerebras.ai',
    'generativelanguage.googleapis.com', 'integrate.api.nvidia.com',
    'api.podcastindex.org', 'huggingface.co',
))


@dataclass(frozen=True)
class DomainIdentity:
    """Instance host and its registrable domain, for masking mentions of the
    instance's domain that appear in strings outside of URLs."""
    host: str
    registrable_domain: str | None

    @property
    def first_label(self) -> str | None:
        return self.registrable_domain.split('.')[0] if self.registrable_domain else None


def build_domain_identity(base_host: str) -> DomainIdentity | None:
    """Derive the registrable domain (last two labels) from BASE_URL's host.
    Returns None for IPs or hosts with fewer than two labels."""
    host = (base_host or '').lower()
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    labels = host.split('.')
    if len(labels) < 2:
        return None
    return DomainIdentity(host=host, registrable_domain='.'.join(labels[-2:]))


def _mask_pii(value: str, domain_identity: DomainIdentity | None) -> str:
    """Mask email addresses and, if given, mentions of the instance's domain."""
    result = _EMAIL_RE.sub('<email>', value)
    if domain_identity is None:
        return result
    for needle in (domain_identity.host, domain_identity.registrable_domain):
        if needle:
            result = re.sub(re.escape(needle), '<domain>', result, flags=re.IGNORECASE)
    first_label = domain_identity.first_label
    if first_label and len(first_label) >= 6:
        result = re.sub(rf'\b{re.escape(first_label)}\b', '<domain>', result, flags=re.IGNORECASE)
    return result


def _normalize_key(key: str) -> str:
    return key.replace('_', '').lower()


def _is_secret_key(key: str) -> bool:
    normalized = _normalize_key(key)
    return normalized.endswith('key') or any(
        s in normalized for s in _REDACT_KEY_SUBSTRINGS)


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


def _is_private_host(hostname: str) -> bool:
    """LAN/loopback/no-domain hosts that should never appear in an export."""
    if not hostname:
        return False
    if hostname == 'localhost' or hostname.endswith(('.local', '.lan', '.internal')):
        return True
    if '.' not in hostname:
        return True
    try:
        return ipaddress.ip_address(hostname).is_private
    except ValueError:
        return False


def _redact_string(value: str, instance_hosts: frozenset, in_settings: bool = False,
                    domain_identity: DomainIdentity | None = None) -> str:
    parts = urlsplit(value)
    if parts.scheme in ('http', 'https') and parts.netloc:
        hostname = (parts.hostname or '').lower()
        if hostname in instance_hosts:
            result = f'{parts.scheme}://<domain>{parts.path}'
        elif _is_private_host(hostname):
            result = f'{parts.scheme}://<private-host>{parts.path}'
        elif in_settings and hostname not in _PUBLIC_PROVIDER_HOSTS:
            result = f'{parts.scheme}://<private-host>{parts.path}'
        else:
            result = _strip_credential_query(value)
    else:
        result = value
    return _mask_pii(result, domain_identity)


def _redact_webhook_url(value: str, instance_hosts: frozenset,
                         domain_identity: DomainIdentity | None = None) -> str:
    parts = urlsplit(value)
    if parts.scheme in ('http', 'https') and parts.netloc:
        hostname = (parts.hostname or '').lower()
        if hostname in instance_hosts:
            return _mask_pii(f'{parts.scheme}://<domain>', domain_identity)
        if _is_private_host(hostname):
            return _mask_pii(f'{parts.scheme}://<private-host>', domain_identity)
    return _mask_pii(safe_url_for_log(value), domain_identity)


def redact_config(value, instance_hosts=frozenset(), in_settings=False, domain_identity=None):
    """Recursively strip secret-shaped keys and credential-shaped URL parts.
    instance_hosts become <domain>; inside the settings section every host that is
    not a known public provider becomes <private-host> (see _PUBLIC_PROVIDER_HOSTS).
    domain_identity, if given, additionally masks email addresses and mentions of
    the instance's domain anywhere in a string, not just in URLs (see _mask_pii)."""
    if isinstance(value, dict):
        webhook = _is_webhook_entry(value)
        out = {}
        for k, v in value.items():
            if isinstance(v, bool):
                out[k] = v
                continue
            if _is_secret_key(k):
                continue
            if webhook and k == 'url' and isinstance(v, str):
                out[k] = _redact_webhook_url(v, instance_hosts, domain_identity)
                continue
            child_in_settings = True if k == 'settings' else in_settings
            out[k] = redact_config(v, instance_hosts, child_in_settings, domain_identity)
        return out
    if isinstance(value, list):
        return [redact_config(v, instance_hosts, in_settings, domain_identity) for v in value]
    if isinstance(value, str):
        return _redact_string(value, instance_hosts, in_settings, domain_identity)
    return value
