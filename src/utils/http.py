"""HTTP utility helpers.

The `post_with_retry` / `get_with_retry` wrappers that lived here were
removed after the 2.0 security audit; every outbound caller now routes
through ``utils.safe_http`` so the per-redirect SSRF revalidation and
downgrade guards apply. Only the log-scrubbing helper remains here.
"""
from urllib.parse import urlsplit


def safe_url_for_log(url, keep_path: bool = False) -> str:
    """Return a safe-for-logs URL string.

    Default: ``scheme://host`` only. Query strings and paths often carry
    credentials or identifiers and are dropped. Set ``keep_path=True``
    to include the path (useful for LLM endpoint logs where the operator
    wants to see ``/v1/chat/completions`` etc.). Query and fragment are
    still dropped when ``keep_path=True``.

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
        if keep_path and parts.path:
            out += parts.path
        return out
    except (TypeError, ValueError):
        return '<url>'
