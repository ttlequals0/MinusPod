"""Consolidated outbound HTTP fetcher with trust tiers and streaming caps.

Two trust tiers:

- ``OPERATOR_CONFIGURED``: admin-typed URLs (LLM base URL, webhook URL,
  operator-configured RSS source). Allows private/loopback; blocks cloud
  metadata, multicast, and reserved.
- ``FEED_CONTENT``: URLs parsed out of fetched RSS (artwork, enclosures).
  Blocks every private range.

Defenses layered on top of the tier check:

- Per-hop redirect revalidation (the Session subclass below rechecks every
  redirect target against the tier rules before allowing the follow).
- HTTPS -> HTTP downgrade blocked at every tier.
- Validates the final URL every request so a compromised DNS lookup cannot
  turn a registered hostname into a private IP mid-flight.
- DNS-rebinding defense: every request (and every redirect hop) resolves the
  hostname once, validates all resolved addresses, and connects to the pinned
  address with the URL, Host header, SNI, and certificate verification left on
  the original hostname. Set ``SSRF_IP_PINNING=false`` to fall back to the
  stock adapters and validate-then-fetch behavior.
"""

from __future__ import annotations

import enum
import logging
import os
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import requests

from config import HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_API, HTTP_TIMEOUT_FETCH
from utils.pinned_transport import PinnedHTTPAdapter
from utils.url import SSRFError, validate_base_url, validate_url

logger = logging.getLogger(__name__)


class URLTrust(enum.Enum):
    OPERATOR_CONFIGURED = "operator_configured"
    FEED_CONTENT = "feed_content"


class ResponseTooLargeError(Exception):
    """Raised when a streamed response exceeds the caller-supplied cap."""


class IncompleteResponseError(Exception):
    """Raised when a body ends before its declared Content-Length."""


@dataclass
class FetchResult:
    """Distinguishes success, size-cap rejection, and network failure so
    callers can emit structured log events without conflating them."""

    ok: bool
    status_code: int | None
    content: bytes | None
    error: str | None
    size_capped: bool = False


class _ChunkedResponse(Protocol):
    headers: object

    def iter_content(self, chunk_size: int) -> object: ...


def read_response_capped(
    response: _ChunkedResponse, max_bytes: int, chunk_size: int = 65536
) -> bytes:
    """Stream a response body, raising above max_bytes or on a short read:
    a connection truncated mid-body ends iter_content without raising."""
    buf = bytearray()
    for chunk in response.iter_content(chunk_size=chunk_size):
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


def stream_to_file_capped(response, fh, max_bytes, *, already=0, chunk_size=8192):
    """Stream a response body into file handle ``fh`` with a hard total byte cap
    (counting ``already`` bytes already on disk). Raises ``ResponseTooLargeError``
    if exceeded. Returns total bytes written."""
    total = already
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ResponseTooLargeError(f"stream exceeds {max_bytes} bytes")
        fh.write(chunk)
    return total


def _validate_for_tier(url: str, trust: URLTrust) -> None:
    """Run the tier-appropriate SSRF validator. Raises ``SSRFError`` on reject."""
    if trust is URLTrust.OPERATOR_CONFIGURED:
        validate_base_url(url)
    else:
        validate_url(url)


def _reject_https_downgrade(original: str, target: str) -> None:
    if urlparse(original).scheme.lower() == 'https' and urlparse(target).scheme.lower() != 'https':
        raise SSRFError(f"HTTPS -> HTTP redirect blocked: {target}")


class _RevalidatingSession(requests.Session):
    """Session subclass that revalidates every redirect hop against the
    configured trust tier and blocks HTTPS -> HTTP downgrades."""

    def __init__(self, trust: URLTrust, max_redirects: int):
        super().__init__()
        self._trust = trust
        self.max_redirects = max_redirects
        # Explicit falsey list, not coerce_bool_setting: unknown values must
        # keep the security control ON (fail secure), not fall back to False.
        pinning = os.environ.get('SSRF_IP_PINNING', 'true').strip().lower()
        if pinning not in ('0', 'false', 'no', 'off'):
            adapter = PinnedHTTPAdapter(
                allow_private=trust is URLTrust.OPERATOR_CONFIGURED
            )
            self.mount('http://', adapter)
            self.mount('https://', adapter)

    def rebuild_auth(self, prepared_request, response):
        original_host = urlparse(response.request.url).hostname if response.request else None
        super().rebuild_auth(prepared_request, response)
        target = prepared_request.url
        _reject_https_downgrade(response.url, target)
        _validate_for_tier(target, self._trust)
        # requests' rebuild_auth strips Authorization on a cross-host redirect
        # but not provider-specific auth headers; drop those too so a 3xx to an
        # attacker-controlled host cannot exfiltrate an API key (creds-5).
        if original_host and urlparse(target).hostname != original_host:
            for header in ('x-api-key', 'api-key'):
                prepared_request.headers.pop(header, None)


def safe_get(
    url: str,
    trust: URLTrust,
    *,
    max_redirects: int = HTTP_MAX_REDIRECTS_API,
    timeout: float = HTTP_TIMEOUT_FETCH,
    stream: bool = False,
    headers: dict | None = None,
) -> requests.Response:
    """GET ``url`` via a session that revalidates every redirect hop.

    Raises ``SSRFError`` for disallowed URLs (initial or redirect targets)
    and ``requests.RequestException`` for network errors. Callers apply
    ``read_response_capped`` on the returned response to enforce size.
    """
    _validate_for_tier(url, trust)
    session = _RevalidatingSession(trust, max_redirects)
    try:
        return session.get(url, timeout=timeout, stream=stream, headers=headers)
    finally:
        if not stream:
            session.close()


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
) -> requests.Response:
    """POST ``url`` via a session that revalidates every redirect hop.

    Webhooks and other outbound POSTs commonly follow redirects; this
    wrapper runs the same trust-tier revalidation on every hop as
    ``safe_get`` does. Raises ``SSRFError`` on rejected URLs. With
    ``stream=True`` the caller owns closing the response (same contract
    as ``safe_get``).
    """
    _validate_for_tier(url, trust)
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
        if not stream:
            session.close()
