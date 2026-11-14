"""Compute a Podcast Namespace `podcast:guid` for a served feed URL.

The spec defines a deterministic UUIDv5 over the feed URL with a fixed
namespace. A MinusPod feed is a derivative served at its own URL, so it gets
its own GUID, which keeps aggregators from conflating it with the origin.

Pure helper. No DB, no I/O.

Do not "fix" the normalization. Aggregators have stored the GUIDs this
function produced, so changing the algorithm re-identifies every existing
feed (it looks like the old feed vanished). The namespace is spec-locked and
the scheme and trailing-slash strip matches the spec example; both are needed
for cross-tool agreement.
"""
import uuid


PODCAST_NAMESPACE_GUID = uuid.UUID("ead4c236-bf58-58c6-a2c6-a6b28d128cb6")


def compute_feed_guid(feed_url: str | None) -> str | None:
    """Return the deterministic UUIDv5 for a feed URL, or None for invalid input.

    Normalization: strip surrounding whitespace, the leading http(s):// scheme
    and a single trailing slash; nothing else is touched. An empty result
    returns None so the caller can omit the tag.
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
