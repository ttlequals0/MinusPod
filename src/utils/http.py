"""HTTP utility helpers.

The `post_with_retry` / `get_with_retry` wrappers that lived here were
removed after the 2.0 security audit; every outbound caller now routes
through ``utils.safe_http`` so the per-redirect SSRF revalidation and
downgrade guards apply. Only log-oriented helpers remain here.
"""
import re
import sys
from urllib.parse import urlsplit

from flask import request

_FEED_CREDENTIAL_RE = re.compile(
    r'(?<![0-9a-f])(?:[0-9a-f]{16}\.)?[0-9a-f]{64}(?![0-9a-f])'
)
# Both patterns also match without a query so a long token is consumed once, not rescanned per start.
_QUERY_STRING_RE = re.compile(r'(https?://[^\s?]*)(\?\S+)?')
_PATH_QUERY_RE = re.compile(r"(?<![\w:/.-])(/[^\s?'\"]*)(\?[^\s'\"]+)?")
# Closing punctuation that ends a token ("[/f?k=v]", "/f?k=v, then") is kept out of the redacted query.
_TRAILING_PUNCT = ']),.;'
# Real records are tracebacks of a few KB; beyond this only head and tail are kept.
MAX_REDACT_CHARS = 32768
_redaction_failure_reported = False


def redact_feed_credentials(value: str) -> str:
    return _FEED_CREDENTIAL_RE.sub('[redacted-feed-key]', value)


def _redact_query(match: re.Match) -> str:
    return match.group(1) + '?<redacted>' if match.group(2) else match.group(0)


def _redact_path_query(match: re.Match) -> str:
    query = match.group(2)
    if not query:
        return match.group(0)
    body = query[1:]
    core = body.rstrip(_TRAILING_PUNCT) or body[:1]
    return f"{match.group(1)}?<redacted>{body[len(core):]}"


def scrub_query_strings(text: str) -> str:
    """Replace the query string of any URL embedded in text with ?<redacted>."""
    return _QUERY_STRING_RE.sub(_redact_query, str(text))


def scrub_request_paths(text: str) -> str:
    """Replace the query string of a bare request path (/feed?key=x) with ?<redacted>."""
    return _PATH_QUERY_RE.sub(_redact_path_query, str(text))


def report_redaction_failure(exc: Exception) -> None:
    """Write one stderr line per process; logging is off limits inside a log sink."""
    global _redaction_failure_reported
    if not _redaction_failure_reported:
        _redaction_failure_reported = True
        try:
            print(f"log redaction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        except Exception:
            pass


def redact_log_text(text: str) -> str:
    """Apply every log redaction in a fixed order; never raises."""
    try:
        text = str(text)
        if len(text) > MAX_REDACT_CHARS:
            half = MAX_REDACT_CHARS // 2
            # The tail restarts at a token boundary: a query whose path start was elided would not match.
            tail = text[-half:]
            first_space = re.search(r'\s', tail)
            tail = tail[first_space.start():] if first_space else ''
            text = f"{text[:half]} [{len(text) - half - len(tail)} chars elided] {tail}"
        return redact_feed_credentials(scrub_request_paths(scrub_query_strings(text)))
    except Exception as exc:
        report_redaction_failure(exc)
        return '[log text withheld: redaction failed]'


def client_ip():
    """Client IP after the application's configured ProxyFix policy."""
    return request.remote_addr or ''


def safe_url_for_log(url, keep_path: bool = False,
                     keep_query: bool = False) -> str:
    """Return a safe-for-logs URL string.

    Default: ``scheme://host[:port]`` only; userinfo is always dropped. Query strings and paths often carry
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
        if ':' in host:
            host = f"[{host}]"
        try:
            port = parts.port
        except ValueError:
            port = None
        out = f"{scheme}://{host}" + (f":{port}" if port is not None else "")
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
