"""Log-oriented HTTP helpers. Outbound requests go through ``utils.safe_http``
for per-redirect SSRF revalidation and downgrade guards."""
import re
import sys
from urllib.parse import urlsplit

from flask import request

# Any run of 64 or more hex chars goes, so a key glued to a percent escape or more hex is still caught.
_FEED_CREDENTIAL_RE = re.compile(r'(?<![0-9a-fA-F])(?:[0-9a-fA-F]{16}\.)?[0-9a-fA-F]{64,}')
# Every str.splitlines break; a break does not end a token, so a query continued on the next line is caught.
_LINE_BREAKS = '\n\x0b\x0c\r\x1c\x1d\x1e\x85\u2028\u2029'
# Whitespace that is not a line break: only this ends a token or takes a length cut.
_HSPACE = '\t\x1f \xa0\u1680\u2000-\u200a\u202f\u205f\u3000'
# Each credential shape is its own pass over the whole text, so no pass can hide input from another.
# Userinfo runs from any "//" to the last "@" before the next "/", "?", "#" or horizontal whitespace.
_USERINFO_RE = re.compile(rf'//[^{_HSPACE}/?#]*@')
# A token that holds a "?" is matched from its start, so a token without one is scanned once.
_QUERY_TOKEN_RE = re.compile(rf'(?<![^{_HSPACE}])[^{_HSPACE}?]*\?[^{_HSPACE}]*')
# A slashless "?" still starts a query when a parameter name and "=" follow it.
_NAMED_QUERY_RE = re.compile(rf'\?[^{_HSPACE}?=&/]+=')
# Only a run of these at the very end of a token survives a query redaction.
_CLOSING = "\"'`])}>,.;:!"
_REDACTED = '<redacted>'
_LAST_SPACE_RE = re.compile(rf'.*[{_HSPACE}]', re.DOTALL)
_SPACE_RE = re.compile(rf'[{_HSPACE}]')
# Already redacted text may also be cut at a line break.
_LAST_CUT_RE = re.compile(rf'.*[{_HSPACE}{_LINE_BREAKS}]', re.DOTALL)
_CUT_RE = re.compile(rf'[{_HSPACE}{_LINE_BREAKS}]')
# Real records are tracebacks of a few KB; beyond this only head and tail are kept.
MAX_REDACT_CHARS = 32768
# Longer input is cut to its head and tail before redaction so a huge record costs bounded time.
_MAX_INPUT_CHARS = 1 << 20
# Room for the elision marker, so output never exceeds the cap.
_ELIDE_MARKER_ROOM = 64
_redaction_failure_reported = False


def redact_feed_credentials(value: str) -> str:
    return _FEED_CREDENTIAL_RE.sub('[redacted-feed-key]', value)


def _redact_query_token(match: re.Match) -> str:
    token = match.group(0)
    # No URL or request path has a "?" before its query, so the first "?" of a token with a "/" starts it.
    if '/' in token:
        mark = token.index('?')
    else:
        named = _NAMED_QUERY_RE.search(token)
        if not named:
            return token
        mark = named.start()
    rest = token[mark + 1:]
    # Breaks that end the token are kept, so a redacted line keeps its line structure.
    core = rest.rstrip(_CLOSING + _LINE_BREAKS)
    if not core or (rest.startswith(_REDACTED) and len(core) <= len(_REDACTED)):
        return token
    return f"{token[:mark]}?{_REDACTED}{rest[len(core):]}"


def redact_url_userinfo(text: str) -> str:
    """Redact the userinfo of any URL in text; the user name goes too, as in safe_url_for_log."""
    return _USERINFO_RE.sub(f'//{_REDACTED}@', str(text))


def scrub_query_strings(text: str) -> str:
    """Redact the query of any URL or request path in text, up to the end of its token."""
    return _QUERY_TOKEN_RE.sub(_redact_query_token, str(text))


def report_redaction_failure(exc: Exception) -> None:
    """Write one stderr line per process; logging is off limits inside a log sink."""
    global _redaction_failure_reported
    if not _redaction_failure_reported:
        _redaction_failure_reported = True
        try:
            # The exception text may hold the very secret being redacted.
            print(f"log redaction failed: {type(exc).__name__}", file=sys.stderr)
        except Exception:
            pass


def _redact(text: str, keep_query: bool) -> str:
    text = redact_feed_credentials(redact_url_userinfo(text))
    return text if keep_query else scrub_query_strings(text)


def redact_stored_text(text: str) -> str:
    """Every redaction redact_log_text applies, without its length cap, so a long value loses only its secrets."""
    return _redact(str(text), False)


def _head(text: str, limit: int, redacted: bool = False) -> str:
    """Longest prefix of at most limit chars that ends at the text end or where whitespace follows.

    Line breaks count as whitespace only for already redacted text.
    """
    if len(text) <= limit:
        return text
    match = (_LAST_CUT_RE if redacted else _LAST_SPACE_RE).match(text, 0, limit + 1)
    return text[:match.end() - 1] if match else ''


def _tail(text: str, limit: int, redacted: bool = False) -> str:
    """Longest suffix of at most limit chars that is the whole text or starts with whitespace.

    Line breaks count as whitespace only for already redacted text.
    """
    if len(text) <= limit:
        return text
    match = (_CUT_RE if redacted else _SPACE_RE).search(text, len(text) - limit)
    return text[match.start():] if match else ''


def redact_log_text(text: str, keep_query: bool = False) -> str:
    """Apply every log redaction, then cap the length at whitespace; never raises.

    keep_query skips only the query pass, for a line the operator opted into seeing queries on.
    """
    try:
        text = str(text)
        # Cuts fall on whitespace and no redaction spans whitespace, so each part redacts as in the whole.
        if len(text) > _MAX_INPUT_CHARS:
            head_src, tail_src = _head(text, _MAX_INPUT_CHARS // 2), _tail(text, _MAX_INPUT_CHARS // 2)
            head_text, tail_text = _redact(head_src, keep_query), _redact(tail_src, keep_query)
            total = len(head_text) + len(text) - len(head_src) - len(tail_src) + len(tail_text)
        else:
            head_text = tail_text = _redact(text, keep_query)
            if len(head_text) <= MAX_REDACT_CHARS:
                return head_text
            total = len(head_text)
        return _elide(head_text, tail_text, total, MAX_REDACT_CHARS)
    except Exception as exc:
        report_redaction_failure(exc)
        return '[log text withheld: redaction failed]'


def redact_exc(exc, limit: int | None = None) -> str:
    """redact_log_text of str(exc), cut to limit characters when given."""
    text = redact_log_text(str(exc))
    return text if limit is None else text[:limit]


def _elide(head_text: str, tail_text: str, total: int, limit: int) -> str:
    budget = limit - _ELIDE_MARKER_ROOM
    head = _head(head_text, budget // 2, redacted=True)
    tail = _tail(tail_text, budget - budget // 2, redacted=True)
    return f"{head} [{total - len(head) - len(tail)} chars elided]{tail}"


def cap_log_text(text: str, limit: int = MAX_REDACT_CHARS) -> str:
    """Cut already redacted text to at most limit chars, keeping head and tail; limit must exceed 64."""
    return text if len(text) <= limit else _elide(text, text, len(text), limit)


def client_ip():
    """Client IP after the application's configured ProxyFix policy."""
    return request.remote_addr or ''


def safe_url_for_log(url, keep_path: bool = False,
                     keep_query: bool = False) -> str:
    """Return a safe-for-logs URL string.

    Default: ``scheme://host[:port]`` only. Userinfo and fragments are always
    dropped; paths and queries often carry credentials or identifiers.
    ``keep_path=True`` includes the path. ``keep_query=True`` also keeps the
    query (opt-in: enclosure queries often hold signed CDN tokens or tracking
    ids) and implies ``keep_path``.

    Unparseable or non-string input reduces to ``<url>`` rather than raising.
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

    Empty when the request went straight through, so callers need no special
    case. Reads ``response.history`` (one entry per hop).
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
