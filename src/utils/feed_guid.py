"""Compute a Podcast Namespace `podcast:guid` for a served feed URL.

The Podcast Namespace spec defines a deterministic UUIDv5 over the feed URL
using a fixed namespace constant. A MinusPod feed is a derivative served at
its own URL, so we mint our own GUID instead of reusing the upstream one;
this keeps aggregators from conflating the proxied feed with the origin.

Pure helper. No DB, no I/O.

DO NOT "fix" the normalization here. Once feeds are in the wild, every
subscriber's aggregator has stored the GUID this function produced. Changing
the algorithm (or the normalization rules) silently re-identifies every
existing feed, which looks to aggregators like the old feed disappeared and
a new one took its place. The namespace constant is spec-locked. The
scheme-and-trailing-slash strip is what the spec example uses; both halves
are load-bearing for cross-tool agreement on the same GUID.
"""
import uuid
from typing import Optional


PODCAST_NAMESPACE_GUID = uuid.UUID("ead4c236-bf58-58c6-a2c6-a6b28d128cb6")


def compute_feed_guid(feed_url: Optional[str]) -> Optional[str]:
    """Return the deterministic UUIDv5 for a feed URL, or None for invalid input.

    Normalization:
      - Strip leading scheme (http://, https://). No other URL component
        is touched.
      - Strip a single trailing slash.
      - Strip surrounding whitespace.

    Any normalized name that ends up empty returns None so the caller can
    omit the tag rather than emit a bogus value.
    """
    if not feed_url or not isinstance(feed_url, str):
        return None

    name = feed_url.strip()
    if name.startswith("https://"):
        name = name[len("https://"):]
    elif name.startswith("http://"):
        name = name[len("http://"):]

    if name.endswith("/"):
        name = name[:-1]

    if not name:
        return None

    return str(uuid.uuid5(PODCAST_NAMESPACE_GUID, name))
