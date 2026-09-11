"""HTTP utility helpers.

The `post_with_retry` / `get_with_retry` wrappers that lived here were
removed after the 2.0 security audit; every outbound caller now routes
through ``utils.safe_http`` so the per-redirect SSRF revalidation and
downgrade guards apply. Only log-oriented helpers remain here.
"""
import re
from urllib.parse import urlsplit

from flask import request

_FEED_CREDENTIAL_RE = re.compile(
    r'(?<![0-9a-f])(?:[0-9a-f]{16}\.)?[0-9a-f]{64}(?![0-9a-f])'
)


def redact_feed_credentials(value: str) -> str:
    return _FEED_CREDENTIAL_RE.sub('[redacted-feed-key]', value)


def client_ip():
    """Client IP after the application's configured ProxyFix policy."""
    return request.remote_addr or ''


def safe_url_for_log(url, keep_path: bool = False,
                     keep_query: bool = False) -> str:
    """Return a safe-for-logs URL string.

    Default: ``scheme://host`` only. Query strings and paths often carry
    credentials or identifiers and are dropped. Set ``keep_path=True``
    to include the path (useful for LLM endpoint logs where the operator
    wants to see ``/v1/chat/completions`` etc.).

    ``keep_query=True`` additionally keeps the query string, which on a
    podcast enclosure regularly holds a signed CDN token or a per-listener
    tracking id. It is opt-in for that reason and implies ``keep_path``.
    Fragments are always dropped.

    Tolerant of non-string input (test doubles, None): anything that
    can't be parsed reduces to the sentinel ``<url>`` rather than raising.
    """
    try:
        parts = urlsplit(str(url))
        host = parts.hostname or ''
        scheme = parts.scheme or 'http'
        if not host:
            return '<url>'
        out = f"{scheme}://{host}"
        if (keep_path or keep_query) and parts.path:
            out += parts.path
        if keep_query and parts.query:
            out += f"?{parts.query}"
        return out
    except (TypeError, ValueError):
        return '<url>'


def redirect_chain_for_log(response, keep_query: bool = False) -> list[str]:
    """Indented log lines tracing a response's redirect hops to its final URL.

    Empty when the request went straight through, so a caller can splice it in
    without a special case. Reads ``response.history``, which requests fills
    with one entry per hop.
    """
    lines = []
    history = list(getattr(response, 'history', None) or [])
    # requests resolves relative and scheme-relative Location headers before
    # following them; the next hop's url is that resolved target.
    hops = history + [response]
    for i, hop in enumerate(history, start=1):
        target = getattr(hops[i], 'url', None)
        if not target and getattr(hop, 'headers', None):
            target = hop.headers.get('Location')
        lines.append(
            f"  redirect {i} ({getattr(hop, 'status_code', '?')}): "
            + (safe_url_for_log(target, keep_path=True, keep_query=keep_query)
               if target else '<unknown>'))
    if lines:
        lines.append("  final: " + safe_url_for_log(
            getattr(response, 'url', None), keep_path=True, keep_query=keep_query))
    return lines
