"""HTTP utility helpers.

The `post_with_retry` / `get_with_retry` wrappers that lived here were
removed after the 2.0 security audit; every outbound caller now routes
through ``utils.safe_http`` so the per-redirect SSRF revalidation and
downgrade guards apply. Only the log-scrubbing helper remains here.
"""
from urllib.parse import urlsplit


def safe_url_for_log(url: str) -> str:
    """Return only scheme+host for logging; drops path, query, fragment so
    tokens embedded anywhere in the URL never reach logs."""
    parts = urlsplit(url)
    host = parts.hostname or ''
    scheme = parts.scheme or 'http'
    return f"{scheme}://{host}" if host else '<url>'
