"""Redaction pass for GET /system/config-export: strips secrets from the
settings/feeds/system document so the JSON is safe to attach to a bug report.
"""
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import tldextract

from utils.http import redact_feed_credentials, safe_url_for_log

_REDACT_KEY_SUBSTRINGS = ('secret', 'password', 'passphrase', 'token', 'apikey')
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
_SAFE_PROVIDER_PATHS = frozenset((
    '', '/v1', '/v1/', '/api', '/api/', '/api/v1', '/api/v1/',
    '/v1/chat/completions', '/v1/messages', '/v1/responses',
    '/v1/audio/transcriptions', '/api/chat',
))
_PSL_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True,
)


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
    """Derive host and registrable domain using the bundled PSL snapshot."""
    host = (base_host or '').lower()
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    extracted = _PSL_EXTRACT(host)
    registrable = extracted.top_domain_under_public_suffix or None
    return DomainIdentity(host=host, registrable_domain=registrable)


def _mask_pii(value: str, domain_identity: DomainIdentity | None) -> str:
    """Mask email addresses and, if given, mentions of the instance's domain."""
    result = _EMAIL_RE.sub('<email>', value)
    if domain_identity is None:
        return result

    for needle in (domain_identity.host, domain_identity.registrable_domain):
        if needle:
            result = re.sub(
                rf'(?<![A-Za-z0-9]){re.escape(needle)}(?![A-Za-z0-9])',
                '<domain>', result, flags=re.IGNORECASE,
            )
    first_label = domain_identity.first_label
    if first_label and len(first_label) >= 6:
        result = re.sub(
            rf'(?<![A-Za-z0-9.-]){re.escape(first_label)}(?![A-Za-z0-9.-])',
            '<domain>', result, flags=re.IGNORECASE,
        )
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


def _safe_url(parts, host: str, path: str = '') -> str:
    """Build a URL without userinfo, query, or fragment."""
    hostname = host
    if ':' in hostname and not hostname.startswith('['):
        hostname = f'[{hostname}]'
    if parts.port and host not in ('<domain>', '<private-host>'):
        hostname = f'{hostname}:{parts.port}'
    return urlunsplit((parts.scheme, hostname, path, '', ''))


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
        path = parts.path if in_settings and parts.path in _SAFE_PROVIDER_PATHS else ''
        if hostname in instance_hosts:
            result = _safe_url(parts, '<domain>', path)
        elif _is_private_host(hostname):
            result = _safe_url(parts, '<private-host>', path)
        elif in_settings and hostname not in _PUBLIC_PROVIDER_HOSTS:
            result = _safe_url(parts, '<private-host>', path)
        else:
            result = _safe_url(parts, hostname, path)
    else:
        result = value
    return _mask_pii(redact_feed_credentials(result), domain_identity)


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


def _redact_source_feed_url(value: str, instance_hosts: frozenset,
                            domain_identity: DomainIdentity | None = None) -> str:
    """Keep only the source-feed origin."""
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https') or not parts.netloc:
        return _mask_pii(redact_feed_credentials(value), domain_identity)
    hostname = (parts.hostname or '').lower()
    if hostname in instance_hosts:
        host = '<domain>'
    elif _is_private_host(hostname):
        host = '<private-host>'
    else:
        host = hostname
    return _mask_pii(_safe_url(parts, host), domain_identity)


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
            if webhook and _normalize_key(k) == 'payloadtemplate':
                out['payloadTemplateConfigured'] = bool(v)
                continue
            if _is_secret_key(k):
                continue
            if _normalize_key(k) in ('sourcefeedurl', 'sourceurl') and isinstance(v, str):
                out[k] = _redact_source_feed_url(v, instance_hosts, domain_identity)
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
