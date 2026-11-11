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
    r'(?<![0-9a-fA-F])(?:[0-9a-fA-F]{16}\.)?[0-9a-fA-F]{64}(?![0-9a-fA-F])'
)
# One tokenizer for absolute URLs and bare paths, so a URL is never handled by both passes.
# A path start after a redacted key is blocked as it was after the raw hex, which keeps redaction idempotent.
_URL_RE = re.compile(
    r'(?:(?<![A-Za-z0-9+.-])(?P<scheme>[A-Za-z0-9+.-]+:)|(?<![\w.-])(?<!\[redacted-feed-key\]))'
    r'(?P<lead>//(?P<userinfo>[^\s/?#]*@)?|/)'
    r'(?P<path>[^\s?]*)(?P<query>\?\S+)?'
)
# Only whitespace ends a URL, so a quote cannot hide the rest; a closing run at the token end is kept.
_TRAILING_PUNCT = "\"'`])}>,.;:!"
_REDACTED = '<redacted>'
_LAST_SPACE_RE = re.compile(r'.*\s', re.DOTALL)
_SPACE_RE = re.compile(r'\s')
# Real records are tracebacks of a few KB; beyond this only head and tail are kept.
MAX_REDACT_CHARS = 32768
# Longer input is cut to its head and tail before redaction so a huge record costs bounded time.
_MAX_INPUT_CHARS = 1 << 20
# Room for the elision marker, so output never exceeds MAX_REDACT_CHARS.
_ELIDE_BUDGET = MAX_REDACT_CHARS - 64
_redaction_failure_reported = False


def redact_feed_credentials(value: str) -> str:
    return _FEED_CREDENTIAL_RE.sub('[redacted-feed-key]', value)


def _redact_query(query: str) -> str:
    body = query[1:]
    # An already redacted query is left alone so a second pass is a no-op.
    if body.startswith(_REDACTED) and not body[len(_REDACTED):].rstrip(_TRAILING_PUNCT):
        return query
    core = body.rstrip(_TRAILING_PUNCT) or body[:1]
    return f"?{_REDACTED}{body[len(core):]}"


def _scrub_urls(text, absolute: bool) -> str:
    def replace(match: re.Match) -> str:
        lead = match.group('lead')
        if (bool(match.group('scheme')) and lead != '/') != absolute:
            return match.group(0)
        userinfo, query = match.group('userinfo'), match.group('query')
        if not userinfo and not query:
            return match.group(0)
        # Userinfo is dropped whole, user name included, as safe_url_for_log does.
        lead = f"//{_REDACTED}@" if userinfo else lead
        return (f"{match.group('scheme') or ''}{lead}{match.group('path')}"
                f"{_redact_query(query) if query else ''}")
    return _URL_RE.sub(replace, str(text))


def scrub_query_strings(text: str) -> str:
    """Redact the query string and userinfo of any absolute URL (any scheme) in text."""
    return _scrub_urls(text, absolute=True)


def scrub_request_paths(text: str) -> str:
    """Redact the query string of a bare request path (/feed?key=x) in text."""
    return _scrub_urls(text, absolute=False)


def report_redaction_failure(exc: Exception) -> None:
    """Write one stderr line per process; logging is off limits inside a log sink."""
    global _redaction_failure_reported
    if not _redaction_failure_reported:
        _redaction_failure_reported = True
        try:
            print(f"log redaction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        except Exception:
            pass


def _redact(text: str) -> str:
    return redact_feed_credentials(scrub_request_paths(scrub_query_strings(text)))


def _head(text: str, limit: int) -> str:
    """Longest prefix of at most limit chars that ends at the text end or where whitespace follows."""
    if len(text) <= limit:
        return text
    match = _LAST_SPACE_RE.match(text, 0, limit + 1)
    return text[:match.end() - 1] if match else ''


def _tail(text: str, limit: int) -> str:
    """Longest suffix of at most limit chars that is the whole text or starts with whitespace."""
    if len(text) <= limit:
        return text
    match = _SPACE_RE.search(text, len(text) - limit)
    return text[match.start():] if match else ''


def redact_log_text(text: str) -> str:
    """Apply every log redaction, then cap the length at whitespace; never raises."""
    try:
        text = str(text)
        # Cuts fall on whitespace and no redaction spans whitespace, so each part redacts as in the whole.
        if len(text) > _MAX_INPUT_CHARS:
            head_src, tail_src = _head(text, _MAX_INPUT_CHARS // 2), _tail(text, _MAX_INPUT_CHARS // 2)
            head_text, tail_text = _redact(head_src), _redact(tail_src)
            total = len(head_text) + len(text) - len(head_src) - len(tail_src) + len(tail_text)
        else:
            head_text = tail_text = _redact(text)
            if len(head_text) <= MAX_REDACT_CHARS:
                return head_text
            total = len(head_text)
        head = _head(head_text, _ELIDE_BUDGET // 2)
        tail = _tail(tail_text, _ELIDE_BUDGET - _ELIDE_BUDGET // 2)
        return f"{head} [{total - len(head) - len(tail)} chars elided]{tail}"
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
