"""Consolidated outbound HTTP fetcher with trust tiers and streaming caps.

Two trust tiers:

- ``OPERATOR_CONFIGURED``: admin-typed URLs (LLM base URL, webhook URL,
  operator-configured RSS source). Allows private/loopback; blocks cloud
  metadata, multicast, and reserved.
- ``FEED_CONTENT``: URLs parsed out of fetched RSS (artwork, enclosures).
  Blocks every private range.

Defenses layered on top of the tier check:

- DNS-rebinding defense: resolve once, validate every returned IP, connect to
  the IP with SNI preserved for the original hostname.
- Per-hop redirect revalidation.
- HTTPS -> HTTP downgrade blocked at every tier.
- Retry re-resolves DNS each attempt.

The fetcher implementation and caller migration land in the SSRF commit; this
module currently exposes the shape (trust tiers, redirect caps, streaming-cap
helper) so downstream commits can import against a stable API.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Protocol


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
