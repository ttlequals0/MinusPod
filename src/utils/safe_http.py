"""Consolidated outbound HTTP fetcher with trust tiers and streaming caps.

Two trust tiers:

- ``OPERATOR_CONFIGURED``: admin-typed URLs (LLM base URL, webhook URL,
  operator RSS source). Allows private/loopback; blocks cloud metadata,
  multicast, and reserved.
- ``FEED_CONTENT``: URLs parsed out of fetched RSS (artwork, enclosures).
  Blocks every private range.
- ``FEED_SOURCE_LISTED``: feed source URLs when MINUSPOD_PRIVATE_FEED_HOSTS is set.
  Strict, except listed hosts and addresses may be private.

Layered defenses:

- Every redirect target is revalidated against the tier before it is followed.
- HTTPS -> HTTP downgrade is blocked at every tier.
- The final URL is validated on every request.
- DNS-rebinding defense: each request and redirect hop resolves the hostname
  once, validates all addresses, and connects to the pinned address while the
  URL, Host header, SNI, and certificate check stay on the hostname. Set
  ``SSRF_IP_PINNING=false`` for the stock adapters and validate-then-fetch.
"""

from __future__ import annotations

import enum
import logging
import os
import time
from typing import Protocol
from urllib.parse import urlparse

import requests

from config import HTTP_MAX_REDIRECTS_API, HTTP_MAX_REDIRECTS_FEED, HTTP_TIMEOUT_API, HTTP_TIMEOUT_FETCH
from utils.http import safe_url_for_log
from utils.pinned_transport import PinnedHTTPAdapter
from utils.url import SSRFError, get_private_feed_hosts, validate_base_url, validate_url

logger = logging.getLogger(__name__)


class URLTrust(enum.Enum):
    OPERATOR_CONFIGURED = "operator_configured"
    FEED_CONTENT = "feed_content"
    FEED_SOURCE_LISTED = "feed_source_listed"


class ResponseTooLargeError(Exception):
    """Raised when a streamed response exceeds the caller-supplied cap."""


class IncompleteResponseError(Exception):
    """Raised when a body ends before its declared Content-Length."""


class ResponseDeadlineError(Exception):
    """Raised when a streamed body is still arriving at the caller's deadline."""


class _ChunkedResponse(Protocol):
    headers: object

    def iter_content(self, chunk_size: int) -> object: ...


def read_response_capped(
    response: _ChunkedResponse, max_bytes: int, chunk_size: int = 65536,
    deadline: float | None = None,
) -> bytes:
    """Stream a response body, raising above max_bytes, past a time.monotonic() deadline,
    or on a short read: a connection truncated mid-body ends iter_content without raising."""
    buf = bytearray()
    for chunk in response.iter_content(chunk_size=chunk_size):
        if deadline is not None and time.monotonic() > deadline:
            raise ResponseDeadlineError(f"body still arriving at deadline ({len(buf)} bytes)")
        if not chunk:
            continue
        if len(buf) + len(chunk) > max_bytes:
            raise ResponseTooLargeError(
                f"response exceeds {max_bytes} bytes (had {len(buf)}, chunk {len(chunk)})"
            )
        buf.extend(chunk)

    expected = _declared_length(response)
    if expected is not None and len(buf) < expected:
        raise IncompleteResponseError(
            f"body ended at {len(buf)} of {expected} declared bytes"
        )
    return bytes(buf)


def _declared_length(response: _ChunkedResponse) -> int | None:
    """Declared Content-Length, or None when absent, malformed, or the body was
    content-encoded: iter_content decodes gzip, so the header counts encoded bytes."""
    headers = response.headers
    encoding = (headers.get('Content-Encoding') or '').strip().lower()
    if encoding and encoding != 'identity':
        return None
    raw = headers.get('Content-Length')
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def stream_to_file_capped(response, fh, max_bytes, *, already=0, chunk_size=8192,
                          max_seconds=None, progress=None):
    """Stream a response body into file handle ``fh`` with a hard total byte cap
    (counting ``already`` bytes already on disk). Raises ``ResponseTooLargeError``
    if exceeded, or ``ResponseDeadlineError`` past ``max_seconds``. ``progress`` is
    called as (total_bytes, elapsed_s) at most once a minute.
    Returns total bytes written."""
    total = already
    start = last_progress = time.monotonic()
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(f"stream exceeds {max_bytes} bytes")
        fh.write(chunk)
        now = time.monotonic()
        elapsed = now - start
        if max_seconds is not None and elapsed > max_seconds:
            raise ResponseDeadlineError(
                f"stream exceeded {max_seconds}s budget ({total} bytes in {elapsed:.0f}s)")
        if progress is not None and now - last_progress >= 60:
            last_progress = now
            progress(total, elapsed)
    return total


def _validate_for_tier(url: str, trust: URLTrust, private_hosts=None) -> None:
    """Run the tier-appropriate SSRF validator. Raises ``SSRFError`` on reject."""
    if trust is URLTrust.OPERATOR_CONFIGURED:
        validate_base_url(url)
    elif trust is URLTrust.FEED_SOURCE_LISTED:
        validate_url(url, private_hosts=(
            get_private_feed_hosts() if private_hosts is None else private_hosts))
    else:
        validate_url(url)


def feed_trust() -> URLTrust:
    """SSRF tier for feed URLs: strict by default, MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS opens all,
    MINUSPOD_PRIVATE_FEED_HOSTS only the listed hosts and ranges."""
    opt_in = os.environ.get('MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS', '').strip().lower()
    if opt_in in ('1', 'true', 'yes', 'on'):
        return URLTrust.OPERATOR_CONFIGURED
    if get_private_feed_hosts():
        return URLTrust.FEED_SOURCE_LISTED
    return URLTrust.FEED_CONTENT


def validate_feed_source_url(url: str) -> str:
    """Validate a feed source URL against the tier feed_trust() selects."""
    _validate_for_tier(url, feed_trust())
    return url.strip()


def _reject_https_downgrade(original: str, target: str) -> None:
    if urlparse(original).scheme.lower() == 'https' and urlparse(target).scheme.lower() != 'https':
        raise SSRFError(f"HTTPS -> HTTP redirect blocked: {safe_url_for_log(target)}")


class _RevalidatingSession(requests.Session):
    """Revalidates every redirect hop against the trust tier and blocks HTTPS -> HTTP downgrades."""

    def __init__(self, trust: URLTrust, max_redirects: int):
        super().__init__()
        self._trust = trust
        self._private_hosts = (
            get_private_feed_hosts() if trust is URLTrust.FEED_SOURCE_LISTED else None)
        self.max_redirects = max_redirects
        # Explicit falsey list, not coerce_bool_setting: unknown values must
        # keep the security control ON (fail secure), not fall back to False.
        pinning = os.environ.get('SSRF_IP_PINNING', 'true').strip().lower()
        if pinning not in ('0', 'false', 'no', 'off'):
            adapter = PinnedHTTPAdapter(
                allow_private=trust is URLTrust.OPERATOR_CONFIGURED,
                private_hosts=self._private_hosts,
            )
            self.mount('http://', adapter)
            self.mount('https://', adapter)

    def rebuild_auth(self, prepared_request, response):
        original_host = urlparse(response.request.url).hostname if response.request else None
        super().rebuild_auth(prepared_request, response)
        target = prepared_request.url
        _reject_https_downgrade(response.url, target)
        _validate_for_tier(target, self._trust, self._private_hosts)
        # requests' rebuild_auth strips Authorization on a cross-host redirect
        # but not provider-specific auth headers; drop those too so a 3xx to an
        # attacker-controlled host cannot exfiltrate an API key (creds-5).
        if original_host and urlparse(target).hostname != original_host:
            for header in ('x-api-key', 'api-key', 'x-goog-api-key'):
                prepared_request.headers.pop(header, None)


def new_revalidating_session(
    trust: URLTrust, max_redirects: int = HTTP_MAX_REDIRECTS_API,
) -> requests.Session:
    """Long-lived keep-alive session with per-hop revalidation and connect-time pinning.

    Pass it to ``safe_post(session=...)``; the caller closes it.
    """
    return _RevalidatingSession(trust, max_redirects)


def safe_get(
    url: str,
    trust: URLTrust,
    *,
    max_redirects: int = HTTP_MAX_REDIRECTS_API,
    timeout: float = HTTP_TIMEOUT_FETCH,
    stream: bool = False,
    headers: dict | None = None,
    proxies: dict | None = None,
) -> requests.Response:
    """GET ``url`` via a session that revalidates every redirect hop.

    ``proxies`` is an operator-supplied requests proxy mapping; the origin URL is still validated.

    Raises ``SSRFError`` for disallowed URLs (initial or redirect targets)
    and ``requests.RequestException`` for network errors. Callers apply
    ``read_response_capped`` on the returned response to enforce size.
    """
    # Pre-flight verdict only; PinnedHTTPAdapter re-validates at connect time.
    _validate_for_tier(url, trust)
    session = _RevalidatingSession(trust, max_redirects)
    try:
        return session.get(url, timeout=timeout, stream=stream, headers=headers, proxies=proxies)
    finally:
        if not stream:
            session.close()


def get_feed_content_with_agents(url: str, agents: list[str], accept: str, *,
                                 timeout: float = HTTP_TIMEOUT_API,
                                 extra_headers: dict | None = None,
                                 on_fallback_ok=None) -> requests.Response:
    """Streamed feed-content GET that tries each User-Agent in order, moving on after a 403.

    on_fallback_ok is called when a later agent got a non-error response."""
    if not agents:
        raise ValueError("no User-Agent to fetch with")
    for i, agent in enumerate(agents):
        response = safe_get(
            url,
            trust=URLTrust.FEED_CONTENT,
            max_redirects=HTTP_MAX_REDIRECTS_FEED,
            timeout=timeout,
            stream=True,
            headers={**(extra_headers or {}), 'User-Agent': agent, 'Accept': accept},
        )
        if response.status_code == 403 and i < len(agents) - 1:
            response.close()
            continue
        if i > 0 and on_fallback_ok and response.status_code < 400:
            on_fallback_ok()
        return response


def get_capped(
    url: str,
    trust: URLTrust,
    max_bytes: int,
    *,
    max_redirects: int = HTTP_MAX_REDIRECTS_API,
    timeout: float = HTTP_TIMEOUT_FETCH,
    headers: dict | None = None,
) -> bytes:
    """GET ``url`` and return the body, enforcing a hard byte cap on the
    streamed response so a small compressed payload cannot balloon in memory.
    Always closes the session. Raises ``SSRFError``, ``requests.RequestException``,
    ``ResponseTooLargeError``, or ``IncompleteResponseError``."""
    response = safe_get(
        url, trust, max_redirects=max_redirects, timeout=timeout,
        stream=True, headers=headers,
    )
    try:
        return read_response_capped(response, max_bytes)
    finally:
        response.close()


def safe_head(
    url: str,
    trust: URLTrust,
    *,
    max_redirects: int = HTTP_MAX_REDIRECTS_API,
    timeout: float = HTTP_TIMEOUT_API,
    headers: dict | None = None,
) -> requests.Response:
    """HEAD ``url`` via a session that revalidates every redirect hop."""
    _validate_for_tier(url, trust)
    session = _RevalidatingSession(trust, max_redirects)
    try:
        return session.head(url, timeout=timeout, headers=headers, allow_redirects=True)
    finally:
        session.close()


def safe_post(
    url: str,
    trust: URLTrust,
    *,
    max_redirects: int = HTTP_MAX_REDIRECTS_API,
    timeout: float = HTTP_TIMEOUT_FETCH,
    data=None,
    json=None,
    files=None,
    headers: dict | None = None,
    stream: bool = False,
    session: requests.Session | None = None,
) -> requests.Response:
    """POST ``url`` via a session that revalidates every redirect hop.

    Same per-hop trust-tier revalidation as ``safe_get``. Raises ``SSRFError``
    on rejected URLs. With ``stream=True`` the caller closes the response.
    A caller-supplied ``session`` (from new_revalidating_session) is reused and
    never closed here.
    """
    _validate_for_tier(url, trust)
    owned = session is None
    if owned:
        session = _RevalidatingSession(trust, max_redirects)
    try:
        return session.post(
            url,
            timeout=timeout,
            data=data,
            json=json,
            files=files,
            headers=headers,
            stream=stream,
        )
    finally:
        if owned and not stream:
            session.close()
