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

DNS-rebinding defense (resolving hostname -> IP once, then connecting to the
IP with SNI preserved for the original hostname) is a follow-up iteration;
the current validation layer still catches static private/metadata targets
before any bytes hit the wire.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Optional, Protocol
from urllib.parse import urlparse

import requests

from config import HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_API, HTTP_TIMEOUT_FETCH
from utils.url import SSRFError, validate_base_url, validate_url

logger = logging.getLogger(__name__)


class URLTrust(enum.Enum):
    OPERATOR_CONFIGURED = "operator_configured"
    FEED_CONTENT = "feed_content"


class RedirectContext(enum.Enum):
    AUDIO_ENCLOSURE = "audio_enclosure"
    ARTWORK = "artwork"
    FEED = "feed"
    LLM = "llm"
    WHISPER = "whisper"
    WEBHOOK = "webhook"
    PRICING = "pricing"


REDIRECT_LIMITS: dict[RedirectContext, int] = {
    RedirectContext.AUDIO_ENCLOSURE: 10,
    RedirectContext.ARTWORK: 5,
    RedirectContext.FEED: 5,
    RedirectContext.LLM: 3,
    RedirectContext.WHISPER: 3,
    RedirectContext.WEBHOOK: 3,
    RedirectContext.PRICING: 3,
}


class ResponseTooLargeError(Exception):
    """Raised when a streamed response exceeds the caller-supplied cap."""


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
    def iter_content(self, chunk_size: int) -> object: ...


def read_response_capped(
    response: _ChunkedResponse, max_bytes: int, chunk_size: int = 65536
) -> bytes:
    """Stream a response body, rejecting if total bytes would exceed max_bytes.

    Predictive check (``len(buf) + len(chunk) > max_bytes``) is done before
    extending the buffer, so the cap is enforced on the exact byte count
    rather than at chunk boundaries.
    """
    buf = bytearray()
    for chunk in response.iter_content(chunk_size=chunk_size):
        if not chunk:
            continue
        if len(buf) + len(chunk) > max_bytes:
            raise ResponseTooLargeError(
                f"response exceeds {max_bytes} bytes (had {len(buf)}, chunk {len(chunk)})"
            )
        buf.extend(chunk)
    return bytes(buf)


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

    def rebuild_auth(self, prepared_request, response):
        super().rebuild_auth(prepared_request, response)
        target = prepared_request.url
        _reject_https_downgrade(response.url, target)
        _validate_for_tier(target, self._trust)


def safe_get(
    url: str,
    trust: URLTrust,
    *,
    max_redirects: int = HTTP_MAX_REDIRECTS_API,
    timeout: float = HTTP_TIMEOUT_FETCH,
    stream: bool = False,
    headers: Optional[dict] = None,
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


def safe_head(
    url: str,
    trust: URLTrust,
    *,
    max_redirects: int = HTTP_MAX_REDIRECTS_API,
    timeout: float = HTTP_TIMEOUT_API,
    headers: Optional[dict] = None,
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
    headers: Optional[dict] = None,
) -> requests.Response:
    """POST ``url`` via a session that revalidates every redirect hop.

    Webhooks and other outbound POSTs commonly follow redirects; this
    wrapper runs the same trust-tier revalidation on every hop as
    ``safe_get`` does. Raises ``SSRFError`` on rejected URLs.
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
        )
    finally:
        session.close()
