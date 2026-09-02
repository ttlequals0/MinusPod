"""HTTP utility helpers.

The `post_with_retry` / `get_with_retry` wrappers that lived here were
removed after the 2.0 security audit; every outbound caller now routes
through ``utils.safe_http`` so the per-redirect SSRF revalidation and
downgrade guards apply. Only log-oriented helpers remain here.
"""
from urllib.parse import urlsplit


def client_ip():
    """Real client IP for request logging: first X-Forwarded-For hop when a
    trusted proxy fronts the app, else the socket peer. Flask request context
    required; imported lazily so non-Flask callers of this module stay clean.
    """
    from flask import request
    return request.headers.get('X-Forwarded-For', request.remote_addr)


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
    history = getattr(response, 'history', None) or []
    for i, hop in enumerate(history, start=1):
        target = hop.headers.get('Location') if getattr(hop, 'headers', None) else None
        lines.append(
            f"  redirect {i} ({getattr(hop, 'status_code', '?')}): "
            + (safe_url_for_log(target, keep_path=True, keep_query=keep_query)
               if target else '<unknown>'))
    if lines:
        lines.append("  final: " + safe_url_for_log(
            getattr(response, 'url', None), keep_path=True, keep_query=keep_query))
    return lines
