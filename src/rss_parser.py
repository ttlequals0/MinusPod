"""RSS feed parsing and management."""
import feedparser
import logging
import hashlib
import os
import re
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from typing import Dict, List, Optional
import requests

from urllib.parse import urlparse

from config import APP_USER_AGENT, HTTP_MAX_REDIRECTS_FEED
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as defused_fromstring

from utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from utils.episode_paths import episode_public_url
from utils.feed_guid import compute_feed_guid
from utils.time import parse_iso_datetime
from utils.url import SSRFError
from utils.http import safe_url_for_log
from utils.safe_http import ResponseTooLargeError, URLTrust, read_response_capped, safe_get


_FEED_CONTENT_TYPES = frozenset({
    'application/rss+xml',
    'application/atom+xml',
    'application/xml',
    'text/xml',
    'application/octet-stream',  # common fallback from static hosts
})


def _max_rss_bytes() -> int:
    """Cap the RSS body size the parser is willing to ingest. Default 200 MB
    covers the largest legitimate feeds (3k+ episodes); operators with
    pathological feeds can raise via ``MINUSPOD_MAX_RSS_BYTES``. Floor at
    1 MB so a typo can't starve legitimate feeds."""
    try:
        raw = int(os.environ.get('MINUSPOD_MAX_RSS_BYTES', 200 * 1024 * 1024))
    except ValueError:
        raw = 200 * 1024 * 1024
    return max(1 * 1024 * 1024, raw)


def _feed_trust() -> URLTrust:
    """Strict SSRF tier for feed URLs by default (rss-http-1); opt back into
    private/LAN hosts with MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS=true."""
    opt_in = os.environ.get('MINUSPOD_ALLOW_PRIVATE_FEED_HOSTS', '').strip().lower()
    if opt_in in ('1', 'true', 'yes', 'on'):
        return URLTrust.OPERATOR_CONFIGURED
    return URLTrust.FEED_CONTENT


def _content_type_looks_like_feed(header_value: str | None) -> bool:
    """Accept anything that plausibly carries RSS / Atom bytes.

    Missing header is permissive because many legacy RSS hosts send no
    Content-Type at all; explicit HTML or binary types are rejected so a
    compromised aggregator cannot feed us arbitrary bytes and hope
    feedparser does something interesting with them.
    """
    if not header_value:
        return True
    main_type = header_value.split(';', 1)[0].strip().lower()
    if not main_type:
        return True
    return main_type in _FEED_CONTENT_TYPES

logger = logging.getLogger(__name__)

# Per-host circuit breakers for upstream RSS feed fetching.
# Keyed by hostname so one failing server doesn't block unrelated feeds.
# Grows one entry per unique host; acceptable since podcast count is bounded.
_rss_circuit_breakers: Dict[str, CircuitBreaker] = {}


def _get_rss_circuit_breaker(url: str) -> CircuitBreaker:
    """Get or create a circuit breaker for the given URL's host."""
    host = urlparse(url).hostname or url
    if host not in _rss_circuit_breakers:
        _rss_circuit_breakers[host] = CircuitBreaker(
            f"rss-{host}", failure_threshold=5, recovery_timeout=60
        )
    return _rss_circuit_breakers[host]


# Podcasting 2.0 channel-tag dispositions. See docs/podcasting-2.0.md for the
# full pass/regenerate/strip rationale. These sets are authoritative.
#
# The spec recognizes several URI strings as the SAME namespace. We must
# accept all of them on parse, because real-world feeds use the different
# forms interchangeably (notably the reference pc20.xml feed declares the
# GitHub-blob form, not the podcastindex.org form). We always emit the
# canonical URI on the root xmlns declaration regardless of which the
# upstream feed used.
_PODCAST_NS_CANONICAL = "https://podcastindex.org/namespace/1.0"
_PODCAST_NS_URIS = (
    _PODCAST_NS_CANONICAL,
    "http://podcastindex.org/namespace/1.0",
    "https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md",
    "http://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md",
    "https://github.com/Podcastindex-org/podcast-namespace/blob/master/docs/1.0.md",
    "http://github.com/Podcastindex-org/podcast-namespace/blob/master/docs/1.0.md",
)

# Channel-level podcast:* tags MinusPod copies from the upstream feed unchanged.
# Excludes: guid (we mint our own), locked (handled with a default), txt
# (filtered by purpose), and anything in _PC2_CHANNEL_STRIP.
# Note: ``images`` (plural) was deprecated by the namespace in favor of
# ``image``. Kept for upstream feeds still emitting the deprecated plural;
# unescaped passthrough is harmless to readers that ignore unknown tags.
# ``block`` and ``complete`` are publisher distribution / lifecycle metadata
# that stays true through ad removal: ``block`` may carry multiple instances
# with an ``id`` attribute scoping the block to specific directories
# (apple/spotify/amazon), and ``complete`` is a boolean show-finished flag.
_PC2_CHANNEL_PASSTHROUGH = frozenset({
    "funding", "podroll", "license", "medium", "person",
    "updateFrequency", "season", "episode", "trailer",
    "images", "image", "socialInteract",
    "value", "valueRecipient", "valueTimeSplit",
    "block", "complete",
})

# Channel-level podcast:* tags MinusPod always removes. soundbite/liveItem/
# alternateEnclosure/source/integrity describe original-audio bytes or
# timeline and would lie about the re-cut file; podping publishes the feed
# URL to a public blockchain (wrong for private re-feeds).
_PC2_CHANNEL_STRIP = frozenset({
    "guid",
    "soundbite", "liveItem", "alternateEnclosure", "source", "integrity",
    "podping",
})

# podcast:txt purpose values that MinusPod refuses to forward.
# verify/applepodcastsverify are ownership tokens bound to the original
# publisher; ai-content is always re-asserted (true) by MinusPod itself.
_PC2_TXT_STRIP_PURPOSES = frozenset({
    "verify", "applepodcastsverify", "ai-content",
})

# iTunes channel-level tag dispositions. Apple Podcasts and most podcast
# apps require several of these to ingest the feed at all (no author/
# category/explicit -> rejected or hidden). Excluded from the allowlist:
#   image: emitted separately with the corrected artwork URL.
#   new-feed-url: would tell apps to redirect MinusPod subscribers to the
#     upstream feed. Mandatory strip; never carry through.
_ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_ITUNES_CHANNEL_PASSTHROUGH = frozenset({
    "author", "summary", "subtitle", "owner",
    "explicit", "category", "keywords",
    "type", "block", "complete",
})

# Standard RSS 2.0 channel tags MinusPod carries through verbatim.
# Excludes title/link/description/language/image (already emitted) and
# lastBuildDate/generator (regenerated by us).
_RSS_CHANNEL_PASSTHROUGH = frozenset({
    "copyright", "managingEditor", "webMaster", "category",
    "pubDate", "ttl", "docs",
})


def _is_podcast_element(elem) -> bool:
    """True if elem belongs to the Podcast Namespace (either URI variant)."""
    tag = getattr(elem, "tag", "")
    if not isinstance(tag, str) or not tag.startswith("{"):
        return False
    end = tag.find("}")
    if end == -1:
        return False
    return tag[1:end] in _PODCAST_NS_URIS


def _podcast_localname(elem) -> str:
    """Return the local name of a {ns}local element, or the raw tag if unnamespaced."""
    tag = getattr(elem, "tag", "")
    if not isinstance(tag, str) or not tag.startswith("{"):
        return tag
    end = tag.find("}")
    return tag[end + 1:] if end != -1 else tag


_ENCLOSURE_PREFIX_RE = re.compile(r'<enclosure url="([^"]+)/episodes/')


def extract_cached_base_url(cached_rss: str) -> Optional[str]:
    """Return the BASE_URL prefix used to render a cached RSS, or None.

    Used by serve_rss to detect a BASE_URL change since the cache was written
    and force a refresh (issue #193). Owned here so format changes to the
    enclosure shape stay co-located with the rendering code.
    """
    m = _ENCLOSURE_PREFIX_RE.search(cached_rss)
    return m.group(1) if m else None


class RSSParser:
    def __init__(self, base_url: str = None):
        self._explicit_base_url = base_url
        self.base_url = base_url or os.getenv('BASE_URL', 'http://localhost:8000')

    def _resolved_base_url(self) -> str:
        """Resolve BASE_URL per call (issue #193).

        Order: explicit constructor arg > env > self.base_url > localhost.
        Reading per call instead of mutating self.base_url avoids a
        shared-state write on the singleton parser used across gunicorn
        threads.
        """
        if getattr(self, '_explicit_base_url', None) is not None:
            return self._explicit_base_url
        env = os.getenv('BASE_URL')
        if env is not None:
            return env
        return getattr(self, 'base_url', 'http://localhost:8000')

    def fetch_feed(self, url: str, timeout: int = 30) -> Optional[str]:
        """Fetch RSS feed from URL."""
        try:
            _get_rss_circuit_breaker(url).check()
        except CircuitBreakerOpen as e:
            logger.debug(f"RSS fetch skipped: {e}")
            return None

        try:
            logger.info(f"Fetching RSS feed from: {safe_url_for_log(url)}")
            # APP_USER_AGENT is required: some feed hosts (e.g.
            # feeds.podcastindex.org) reject the default python-requests UA
            # with 403. fetch_feed_conditional already sets it; the
            # initial-fetch path was previously missing it, which made
            # add_feed slug derivation fail on UA-strict hosts.
            response = safe_get(
                url,
                trust=_feed_trust(),
                timeout=timeout,
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                stream=True,
                headers={'User-Agent': APP_USER_AGENT},
            )
            response.raise_for_status()
            if not _content_type_looks_like_feed(response.headers.get('Content-Type')):
                logger.warning(
                    "RSS fetch rejected on content-type: url=%s content_type=%r",
                    url, response.headers.get('Content-Type'),
                )
                _get_rss_circuit_breaker(url).record_failure()
                return None
            max_bytes = _max_rss_bytes()
            try:
                body = read_response_capped(response, max_bytes)
            except ResponseTooLargeError:
                logger.warning(
                    "feed_size_cap_exceeded: url=%s max=%d",
                    safe_url_for_log(url), max_bytes,
                )
                _get_rss_circuit_breaker(url).record_failure()
                return None
            logger.info(f"Successfully fetched RSS feed, size: {len(body)} bytes")
            _get_rss_circuit_breaker(url).record_success()
            return body.decode('utf-8', errors='replace')
        except SSRFError as e:
            logger.warning(f"SSRF blocked in fetch_feed: {e} (url={safe_url_for_log(url)})")
            return None
        except requests.exceptions.ContentDecodingError as e:
            # Some servers claim gzip encoding but send malformed data
            # Retry without accepting compressed responses
            logger.warning(f"Gzip decompression failed, retrying without compression: {e}")
            try:
                response = safe_get(
                    url,
                    trust=_feed_trust(),
                    timeout=timeout,
                    max_redirects=HTTP_MAX_REDIRECTS_FEED,
                    stream=True,
                    headers={
                        'Accept-Encoding': 'identity',
                        'User-Agent': APP_USER_AGENT,
                    },
                )
                response.raise_for_status()
                max_bytes = _max_rss_bytes()
                try:
                    body = read_response_capped(response, max_bytes)
                except ResponseTooLargeError:
                    logger.warning("feed_size_cap_exceeded: url=%s max=%d",
                                   safe_url_for_log(url), max_bytes)
                    _get_rss_circuit_breaker(url).record_failure()
                    return None
                finally:
                    response.close()
                logger.info(f"Successfully fetched RSS feed (uncompressed), size: {len(body)} bytes")
                _get_rss_circuit_breaker(url).record_success()
                return body.decode('utf-8', errors='replace')
            except (requests.RequestException, SSRFError) as retry_e:
                logger.error(f"Failed to fetch RSS feed (retry): {retry_e}")
                _get_rss_circuit_breaker(url).record_failure()
                return None
        except requests.RequestException as e:
            logger.error(f"Failed to fetch RSS feed: {e}")
            _get_rss_circuit_breaker(url).record_failure()
            return None

    def fetch_feed_conditional(self, url: str, etag: str = None,
                               last_modified: str = None, timeout: int = 30):
        """Fetch RSS feed with conditional GET support.

        Uses If-None-Match and If-Modified-Since headers to avoid downloading
        unchanged feeds, reducing bandwidth and server load.

        Args:
            url: RSS feed URL
            etag: Previously received ETag header value
            last_modified: Previously received Last-Modified header value
            timeout: Request timeout in seconds

        Returns:
            Tuple of (content, new_etag, new_last_modified)
            If feed not modified (304), returns (None, etag, last_modified)
            On error, returns (None, None, None)
        """
        headers = {'User-Agent': APP_USER_AGENT}
        if etag:
            headers['If-None-Match'] = etag
        if last_modified:
            headers['If-Modified-Since'] = last_modified

        try:
            _get_rss_circuit_breaker(url).check()
        except CircuitBreakerOpen as e:
            logger.debug(f"RSS conditional fetch skipped: {e}")
            return None, None, None

        try:
            response = safe_get(
                url,
                trust=_feed_trust(),
                timeout=timeout,
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                stream=True,
                headers=headers,
            )

            if response.status_code == 304:
                logger.debug(f"Feed not modified (304): {safe_url_for_log(url)}")
                _get_rss_circuit_breaker(url).record_success()
                response.close()
                return None, etag, last_modified

            response.raise_for_status()
            if not _content_type_looks_like_feed(response.headers.get('Content-Type')):
                logger.warning(
                    "RSS conditional fetch rejected on content-type: url=%s content_type=%r",
                    url, response.headers.get('Content-Type'),
                )
                response.close()
                _get_rss_circuit_breaker(url).record_failure()
                return None, None, None

            new_etag = response.headers.get('ETag')
            new_last_modified = response.headers.get('Last-Modified')

            max_bytes = _max_rss_bytes()
            try:
                body = read_response_capped(response, max_bytes)
            except ResponseTooLargeError:
                logger.warning("feed_size_cap_exceeded: url=%s max=%d",
                               safe_url_for_log(url), max_bytes)
                _get_rss_circuit_breaker(url).record_failure()
                return None, None, None
            finally:
                response.close()

            logger.debug(f"Fetched RSS feed, size: {len(body)} bytes")
            _get_rss_circuit_breaker(url).record_success()
            return body.decode('utf-8', errors='replace'), new_etag, new_last_modified

        except SSRFError as e:
            logger.warning(f"SSRF blocked in fetch_feed_conditional: {e} (url={safe_url_for_log(url)})")
            return None, None, None

        except requests.exceptions.ContentDecodingError as e:
            # Retry without accepting compressed responses
            logger.warning(f"Gzip decompression failed, retrying: {e}")
            try:
                headers['Accept-Encoding'] = 'identity'
                response = safe_get(
                    url,
                    trust=_feed_trust(),
                    timeout=timeout,
                    max_redirects=HTTP_MAX_REDIRECTS_FEED,
                    stream=True,
                    headers=headers,
                )
                if response.status_code == 304:
                    _get_rss_circuit_breaker(url).record_success()
                    response.close()
                    return None, etag, last_modified
                response.raise_for_status()
                new_etag = response.headers.get('ETag')
                new_last_modified = response.headers.get('Last-Modified')
                max_bytes = _max_rss_bytes()
                try:
                    body = read_response_capped(response, max_bytes)
                except ResponseTooLargeError:
                    logger.warning("feed_size_cap_exceeded: url=%s max=%d",
                                   safe_url_for_log(url), max_bytes)
                    _get_rss_circuit_breaker(url).record_failure()
                    return None, None, None
                finally:
                    response.close()
                _get_rss_circuit_breaker(url).record_success()
                return (
                    body.decode('utf-8', errors='replace'),
                    new_etag,
                    new_last_modified,
                )
            except (SSRFError, requests.RequestException):
                _get_rss_circuit_breaker(url).record_failure()
                return None, None, None

        except requests.RequestException as e:
            logger.error(f"Conditional fetch failed: {e}")
            _get_rss_circuit_breaker(url).record_failure()
            return None, None, None

    def parse_feed(self, feed_content: str) -> Dict:
        """Parse RSS feed content.

        XXE defence: ``defusedxml.defuse_stdlib()`` neutralises expat's
        DOCTYPE / ENTITY handling at parse time, but feedparser swallows
        the typed exception and surfaces it as a generic
        SAXParseException('syntax error'). To surface a useful operator
        signal, pre-scan the raw bytes for DOCTYPE / ENTITY markers and
        emit the structured ``xml_forbidden_construct`` event BEFORE
        handing the payload to feedparser.
        """
        try:
            # Normalise to bytes for the pre-scan; feedparser accepts either.
            if isinstance(feed_content, str):
                header_bytes = feed_content.encode('utf-8', errors='ignore')
            else:
                header_bytes = feed_content
            # Scan the first 64 KB; legitimate feeds declare their prolog
            # up front, but some megabyte-scale feeds carry a chunk of XML
            # comments / BOM-prefixed iTunes blocks before the DOCTYPE
            # would appear. 64 KB stays cheap (lowercasing ~65k bytes is
            # microseconds) while catching pathological cases the 4 KB
            # window missed.
            header = header_bytes[:65536].lower()
            if b'<!doctype' in header or b'<!entity' in header:
                construct = 'DOCTYPE' if b'<!doctype' in header else 'ENTITY'
                logger.warning(
                    "XML forbidden construct in feed: %s",
                    construct,
                    extra={
                        'event': 'xml_forbidden_construct',
                        'construct': construct,
                    },
                )
                return None

            feed = feedparser.parse(feed_content)
            if feed.bozo:
                logger.warning(f"RSS parse warning: {feed.bozo_exception}")

            logger.debug(f"Parsed RSS feed: {feed.feed.get('title', 'Unknown')} with {len(feed.entries)} entries")
            return feed
        except Exception as e:
            logger.error(f"Failed to parse RSS feed: {e}")
            return None

    @staticmethod
    def extract_podcast_artwork_url(feed_content_or_parsed) -> Optional[str]:
        """Channel-level podcast artwork URL.

        feedparser flattens ``<itunes:image>`` across the whole document, so
        ``parsed_feed.feed.image.href`` gets clobbered by the LAST itunes:image
        encountered (typically a per-episode override) instead of the
        channel-level one. The Podcasting 2.0 reference feed pc20.xml is a
        live example: channel ``<image><url>`` is a 144x144 PNG, but
        feedparser surfaces the 40 MB per-episode GIF.

        Parse the raw XML directly so we only consider channel-level
        elements. Order of preference:

          1. ``<itunes:image href="...">`` as a direct child of ``<channel>``
          2. ``<image><url>`` as a direct child of ``<channel>``

        Accepts either raw bytes/str (preferred) or a feedparser parse
        result (legacy compat); the legacy path is intentionally narrow
        because it carries the bug described above.
        """
        if not feed_content_or_parsed:
            return None

        if isinstance(feed_content_or_parsed, (str, bytes)):
            try:
                payload = (feed_content_or_parsed.encode('utf-8')
                           if isinstance(feed_content_or_parsed, str)
                           else feed_content_or_parsed)
                root = defused_fromstring(payload)
            except Exception:
                return None

            channel = None
            for child in list(root) if root is not None else []:
                tag = getattr(child, 'tag', '')
                if isinstance(tag, str) and (tag == 'channel' or tag.endswith('}channel')):
                    channel = child
                    break
            if channel is None:
                return None

            ITUNES_NS_TAGS = (
                '{http://www.itunes.com/dtds/podcast-1.0.dtd}image',
                'itunes:image',
            )
            channel_itunes_image = None
            channel_rss_image = None
            for elem in channel:
                tag = getattr(elem, 'tag', '')
                if not isinstance(tag, str):
                    continue
                if tag in ITUNES_NS_TAGS:
                    href = elem.get('href') or ''
                    if href.strip():
                        channel_itunes_image = href.strip()
                        break
                if tag == 'image' or tag.endswith('}image'):
                    for sub in elem:
                        sub_tag = getattr(sub, 'tag', '')
                        if isinstance(sub_tag, str) and (sub_tag == 'url' or sub_tag.endswith('}url')):
                            url_text = (sub.text or '').strip()
                            if url_text:
                                channel_rss_image = url_text
                                break
            return channel_itunes_image or channel_rss_image

        # Legacy feedparser path (kept narrow; see docstring).
        feed = getattr(feed_content_or_parsed, 'feed', None)
        if feed is None:
            return None
        if hasattr(feed, 'image') and hasattr(feed.image, 'href'):
            return feed.image.href
        if 'itunes_image' in feed:
            return feed.itunes_image.get('href')
        return None

    @staticmethod
    def extract_podcast_categories(parsed_feed) -> List[str]:
        """Extract iTunes category strings (top-level + subcategory) from a parsed feed.

        Returns the raw category labels exactly as they appear in the feed.
        Callers map them through `utils.community_tags.map_itunes_category`.
        """
        if not parsed_feed or not parsed_feed.feed:
            return []
        labels: List[str] = []
        feed = parsed_feed.feed
        # feedparser exposes RSS-level categories on .tags as a list of dicts.
        tags = feed.get('tags', []) if hasattr(feed, 'get') else getattr(feed, 'tags', [])
        for t in tags or []:
            label = None
            if isinstance(t, dict):
                label = t.get('term') or t.get('label')
            else:
                label = getattr(t, 'term', None) or getattr(t, 'label', None)
            if label and isinstance(label, str):
                labels.append(label.strip())
        # Dedup while preserving order.
        seen = set()
        out: List[str] = []
        for lab in labels:
            if lab not in seen:
                seen.add(lab)
                out.append(lab)
        return out

    @staticmethod
    def extract_episode_categories(entry) -> List[str]:
        """Extract iTunes category strings from a single feedparser entry."""
        if entry is None:
            return []
        tags = entry.get('tags', []) if hasattr(entry, 'get') else getattr(entry, 'tags', [])
        labels: List[str] = []
        for t in tags or []:
            label = None
            if isinstance(t, dict):
                label = t.get('term') or t.get('label')
            else:
                label = getattr(t, 'term', None) or getattr(t, 'label', None)
            if label and isinstance(label, str):
                labels.append(label.strip())
        seen = set()
        out: List[str] = []
        for lab in labels:
            if lab not in seen:
                seen.add(lab)
                out.append(lab)
        return out

    def generate_episode_id(self, episode_url: str, guid: str = None) -> str:
        """Generate consistent episode ID from GUID or URL.

        Uses RSS GUID if available (stable identifier), falls back to URL
        hash. This prevents duplicate episode IDs when CDNs include
        dynamic tracking parameters in audio URLs (e.g., Megaphone's
        awCollectionId / awEpisodeId).

        The hash is MD5 truncated to 12 hex characters. This is a
        deduplication identifier, not a security hash; MD5's
        cryptographic weaknesses do not apply here. The 48-bit output
        gives a birthday-collision threshold of ~16M episodes per
        instance, well above any real deployment scale. We keep the
        MD5+12 scheme (rather than switching to SHA-256) because
        changing it would invalidate every existing URL in every
        podcast-app subscription of every MinusPod user -- a migration
        cost no attack model justifies. The `is_valid_episode_id`
        validator in `utils.validation` is the load-bearing contract
        (`[0-9a-f]{12}`), not the choice of hash function.
        """
        if guid and guid.strip():
            clean_guid = guid.strip()
            return hashlib.md5(clean_guid.encode()).hexdigest()[:12]
        return hashlib.md5(episode_url.encode()).hexdigest()[:12]

    def modify_feed(self, feed_content: str, slug: str, storage=None,
                    max_episodes: int = 300,
                    extra_episodes: Optional[List[Dict]] = None,
                    processed_only: bool = False,
                    processed_episode_ids: Optional[set] = None,
                    parsed_feed=None,
                    title_override: Optional[str] = None) -> str:
        """Modify RSS feed to use our server URLs.

        Args:
            feed_content: Original RSS feed XML
            slug: Podcast slug
            title_override: When non-empty, replaces the channel title shown to
                subscribers (issue #375) so a processed feed can be told apart
                from the source. Episode titles are unaffected.
            storage: Optional Storage instance for checking Podcasting 2.0 assets
            max_episodes: Max episodes to include in feed (1-500, default 300)
            extra_episodes: Processed episodes from DB to append beyond the cap.
                Each dict must have: episode_id, title, description, published_at,
                new_duration, episode_number.
            processed_only: When True, upstream RSS entries whose episode_id is
                not in processed_episode_ids are dropped from the served feed
                (issue #181). extra_episodes are unaffected (already processed).
            processed_episode_ids: Allow-list of episode_ids the caller has
                determined to be status='processed'. Required when
                processed_only=True; ignored otherwise.
            parsed_feed: Optional pre-parsed feedparser object. When supplied,
                skips the internal parse_feed call - the caller will have
                already paid that cost. Passing a None here re-parses
                feed_content for backwards compatibility.
        """
        feed = parsed_feed if parsed_feed is not None else self.parse_feed(feed_content)
        if not feed:
            return feed_content

        # Build modified RSS with Podcasting 2.0 namespace
        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append('<rss version="2.0" '
                     'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
                     f'xmlns:podcast="{_PODCAST_NS_CANONICAL}">')
        lines.append('<channel>')

        # Copy channel metadata (escape XML entities to prevent invalid XML from & in URLs)
        channel = feed.feed
        # Per-feed title override (#375): the rename must actually change the
        # channel title subscribers see, not just the DB/UI.
        effective_title = title_override if (title_override or '').strip() else channel.get("title", "")
        lines.append(f'<title>{self._escape_xml(effective_title)}</title>')
        lines.append(f'<link>{self._escape_xml(channel.get("link", ""))}</link>')
        lines.append(f'<description><![CDATA[{self._escape_cdata(self._get_channel_description(channel))}]]></description>')
        lines.append(f'<language>{self._escape_xml(channel.get("language", "en"))}</language>')

        # Pass through standard RSS + iTunes channel metadata from upstream
        # (author, category, explicit, owner, etc.). Required by Apple
        # Podcasts and most apps; without these the feed is silently
        # dropped from the directory and artwork won't render.
        self._emit_channel_metadata_passthrough(lines, feed_content)

        # Channel artwork: take the correct channel-level URL from raw XML
        # (feedparser corrupts feed.image.href with per-episode itunes:image
        # overrides). Emit BOTH the standard <image> block and the
        # <itunes:image> tag that Apple Podcasts and most apps prefer.
        artwork_url = self.extract_podcast_artwork_url(feed_content)
        if artwork_url:
            channel_title = effective_title or ''
            channel_link = channel.get('link', '') or ''
            lines.append(f'<image>')
            lines.append(f'  <url>{self._escape_xml(artwork_url)}</url>')
            lines.append(f'  <title>{self._escape_xml(channel_title)}</title>')
            lines.append(f'  <link>{self._escape_xml(channel_link)}</link>')
            lines.append(f'</image>')
            lines.append(f'<itunes:image href="{self._escape_xml(artwork_url)}" />')

        # Channel-level Podcasting 2.0 tags: minted guid, passthrough of safe
        # upstream tags, ai-content disclosure. See docs/podcasting-2.0.md.
        self._emit_channel_pc2_tags(lines, feed_content, slug)

        # Limit to most recent episodes to keep feed size manageable
        # Pocket Casts and other apps may reject very large feeds (>1MB)
        max_episodes = max(1, min(max_episodes, 500))
        entries = feed.entries[:max_episodes]

        if len(feed.entries) > max_episodes:
            logger.debug(f"[{slug}] Limiting feed from {len(feed.entries)} to {max_episodes} episodes")

        # Process each episode from RSS
        included_episode_ids = set()
        for entry in entries:
            episode_url = None
            # Find audio URL in enclosures
            for enclosure in entry.get('enclosures', []):
                if 'audio' in enclosure.get('type', ''):
                    episode_url = enclosure.get('href', '')
                    break

            if not episode_url:
                # Skip entries without audio
                logger.warning(f"Skipping entry without audio: {entry.get('title', 'Unknown')}")
                continue

            episode_id = self.generate_episode_id(episode_url, entry.get('id'))
            if processed_only and episode_id not in (processed_episode_ids or set()):
                continue
            included_episode_ids.add(episode_id)
            modified_url = f"{self._resolved_base_url()}/episodes/{slug}/{episode_id}.mp3"

            lines.append('<item>')
            lines.append(f'  <title>{self._escape_xml(entry.get("title", ""))}</title>')
            lines.append(f'  <description><![CDATA[{self._escape_cdata(self._get_episode_description(entry))}]]></description>')
            lines.append(f'  <link>{self._escape_xml(entry.get("link", ""))}</link>')
            lines.append(f'  <guid>{self._escape_xml(entry.get("id", episode_url))}</guid>')
            lines.append(f'  <pubDate>{self._escape_xml(entry.get("published", ""))}</pubDate>')

            # Modified enclosure URL
            lines.append(f'  <enclosure url="{modified_url}" type="audio/mpeg" />')

            # iTunes specific tags (validate to avoid outputting None as string)
            if 'itunes_duration' in entry:
                duration = entry.itunes_duration
                if duration and str(duration).strip():
                    lines.append(f'  <itunes:duration>{duration}</itunes:duration>')

            if 'itunes_explicit' in entry:
                explicit = entry.itunes_explicit
                if explicit and str(explicit).lower() in ('true', 'false', 'yes', 'no'):
                    lines.append(f'  <itunes:explicit>{explicit}</itunes:explicit>')

            # Episode number (itunes:episode)
            if hasattr(entry, 'itunes_episode'):
                ep_num = entry.itunes_episode
                if ep_num and str(ep_num).strip():
                    lines.append(f'  <itunes:episode>{ep_num}</itunes:episode>')

            # Episode artwork (itunes:image)
            artwork_url = None
            if hasattr(entry, 'image') and hasattr(entry.image, 'href'):
                artwork_url = entry.image.href
            elif 'itunes_image' in entry:
                artwork_url = entry.itunes_image.get('href')
            if artwork_url:
                lines.append(f'  <itunes:image href="{self._escape_xml(artwork_url)}" />')

            # Podcasting 2.0 tags (transcript and chapters). Emits a
            # MinusPod-served URL only when our regenerated file is
            # cached; never falls back to upstream.
            self._append_podcasting2_tags(lines, slug, episode_id, storage)

            lines.append('</item>')

        # Append processed episodes that fell outside the RSS cap
        appended_count = 0
        if extra_episodes:
            for ep in extra_episodes:
                ep_id = ep['episode_id']
                if ep_id in included_episode_ids:
                    continue
                self._append_db_episode_item(lines, slug, ep, storage)
                appended_count += 1

        lines.append('</channel>')
        lines.append('</rss>')

        total_episodes = len(included_episode_ids) + appended_count
        modified_rss = '\n'.join(lines)
        logger.info(f"[{slug}] Modified RSS feed with {total_episodes} episodes ({appended_count} appended from DB)")
        return modified_rss

    def _append_podcasting2_tags(self, lines: list, slug: str, episode_id: str, storage) -> None:
        # Emit only when MinusPod has the cached file. Upstream URLs must
        # never appear in the served feed; see docs/podcasting-2.0.md.
        if not storage:
            return
        base_url = self._resolved_base_url()
        if storage.has_transcript_vtt(slug, episode_id):
            transcript_url = f"{base_url}/episodes/{slug}/{episode_id}.vtt"
            lines.append(f'  <podcast:transcript url="{transcript_url}" type="text/vtt" language="en" rel="captions" />')
        if storage.has_chapters_json(slug, episode_id):
            chapters_url = f"{base_url}/episodes/{slug}/{episode_id}/chapters.json"
            lines.append(f'  <podcast:chapters url="{chapters_url}" type="application/json+chapters" />')

    def _append_db_episode_item(self, lines: list, slug: str, ep: Dict, storage) -> None:
        """Append a single <item> for a processed episode from the database."""
        ep_id = ep['episode_id']
        modified_url = episode_public_url(self._resolved_base_url(), slug, ep_id,
                                           ep.get('processed_version'))
        lines.append('<item>')
        lines.append(f'  <title>{self._escape_xml(ep.get("title") or "Unknown")}</title>')
        if ep.get('description'):
            lines.append(f'  <description><![CDATA[{self._escape_cdata(ep["description"])}]]></description>')
        lines.append(f'  <enclosure url="{modified_url}" type="audio/mpeg" />')
        lines.append(f'  <guid isPermaLink="false">{ep_id}</guid>')
        if ep.get('published_at'):
            lines.append(f'  <pubDate>{self._format_rfc2822(ep["published_at"])}</pubDate>')
        if ep.get('new_duration'):
            lines.append(f'  <itunes:duration>{int(ep["new_duration"])}</itunes:duration>')
        if ep.get('episode_number'):
            lines.append(f'  <itunes:episode>{ep["episode_number"]}</itunes:episode>')
        self._append_podcasting2_tags(lines, slug, ep_id, storage)
        lines.append('</item>')

    def _format_rfc2822(self, iso_date: str) -> str:
        """Convert ISO 8601 date string to RFC 2822 format for RSS pubDate."""
        try:
            dt = parse_iso_datetime(iso_date)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return format_datetime(dt)
        except (ValueError, TypeError, AttributeError):
            return iso_date

    @staticmethod
    def _get_channel_description(channel) -> str:
        """Channel description with iTunes fallback chain.

        Mirrors ``_get_episode_description`` at channel scope. Falls back
        only when the upstream ``<description>`` is empty or whitespace,
        not when it is "short"; publishers who emit a deliberately concise
        description should not be second-guessed.

        Returns raw HTML when iTunes fields carry markup; the caller
        wraps in CDATA.
        """
        desc = channel.get('description', '') or ''
        if desc.strip():
            return desc

        itunes_summary = channel.get('itunes_summary', '') or ''
        if itunes_summary.strip():
            return itunes_summary

        subtitle = channel.get('subtitle', '') or ''
        if subtitle.strip():
            return subtitle

        itunes_subtitle = channel.get('itunes_subtitle', '') or ''
        if itunes_subtitle.strip():
            return itunes_subtitle

        return ''

    @staticmethod
    def _get_episode_description(entry) -> str:
        """Extract episode description with fallback to iTunes fields.

        Many feeds (e.g. Relay FM) leave <description> empty and put the
        actual episode summary in <itunes:subtitle> or <content:encoded>.
        Feedparser exposes these as 'subtitle' and 'content' respectively.

        Note: the returned value may contain raw HTML (especially from
        content:encoded). Callers should wrap in CDATA rather than
        XML-escaping.
        """
        # feedparser aliases <description> and 'summary' to the same value,
        # so checking both is redundant -- just check 'description'.
        desc = entry.get('description', '') or ''
        if desc.strip():
            return desc

        # <itunes:subtitle> -> 'subtitle'
        subtitle = entry.get('subtitle', '') or ''
        if subtitle.strip():
            return subtitle

        # <content:encoded> -> 'content' list (may contain HTML)
        content = entry.get('content', [])
        if content and isinstance(content, list):
            value = content[0].get('value', '') or ''
            if value.strip():
                return value

        return ''

    def _escape_xml(self, text: str) -> str:
        """Escape XML special characters."""
        if not text:
            return ""
        return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))

    @staticmethod
    def _escape_cdata(text) -> str:
        """Make ``text`` safe to embed inside a ``<![CDATA[...]]>`` block.

        CDATA has no character escaping; the only sequence that can break out
        is the terminator ``]]>``. Upstream descriptions are attacker-influenced
        (whatever the source feed publishes), so an unescaped ``]]>`` corrupts
        the generated XML and breaks the served feed for every subscriber. The
        canonical fix splits the terminator across two CDATA sections so the
        literal text is preserved while the parser never sees a real ``]]>``.
        """
        if not text:
            return ""
        return str(text).replace(']]>', ']]]]><![CDATA[>')

    def _serialize_podcast_element(self, elem, _depth: int = 0) -> str:
        # Hand-rolled rather than ``ET.tostring`` because tostring re-declares
        # ``xmlns:podcast`` on every element (the root already declares it)
        # and renames the prefix to ``ns0:``. Non-podcast children are dropped
        # defensively so unrelated namespaces can't leak into a passthrough
        # block.
        #
        # Depth cap: defusedxml does not bound element nesting (only entity
        # expansion), so an upstream feed could nest ``podcast:value`` /
        # ``podcast:valueTimeSplit`` arbitrarily and blow Python's recursion
        # limit. Spec-legal channel-level podcast trees are <= 3 deep
        # (e.g. ``value > valueTimeSplit > valueRecipient``), so 16 is a
        # generous cap that still rejects hostile feeds before they crash
        # the worker.
        if _depth > 16:
            return ""

        local = _podcast_localname(elem)

        attr_parts = []
        for attr_name, attr_value in elem.attrib.items():
            if attr_name.startswith("{"):
                continue
            attr_parts.append(f'{attr_name}="{self._escape_xml(attr_value)}"')
        attr_str = (" " + " ".join(attr_parts)) if attr_parts else ""

        child_xml = []
        for child in elem:
            if _is_podcast_element(child):
                child_xml.append(self._serialize_podcast_element(child, _depth + 1))

        raw_text = elem.text or ""
        text_xml = self._escape_xml(raw_text) if raw_text.strip() else ""

        if not child_xml and not text_xml:
            return f'<podcast:{local}{attr_str} />'
        return f'<podcast:{local}{attr_str}>{text_xml}{"".join(child_xml)}</podcast:{local}>'

    def _parse_upstream_channel_pc2_tags(self, feed_content: str) -> Dict:
        # Never raises. Returns ``{"locked": None, "passthrough": []}`` on any
        # parse failure so the feed still builds with our minted guid and
        # ai-content disclosure even when upstream XML is malformed.
        result = {"locked": None, "passthrough": []}
        if not feed_content:
            return result

        try:
            payload = feed_content.encode("utf-8") if isinstance(feed_content, str) else feed_content
            root = defused_fromstring(payload)
        except DefusedXmlException as e:
            # Mirror the xml_forbidden_construct event in parse_feed so an
            # operator scanning logs sees the same signal at the same level.
            logger.warning(
                "Upstream feed rejected during channel-PC2 parse: %s",
                type(e).__name__,
                extra={
                    'event': 'xml_forbidden_construct',
                    'construct': type(e).__name__,
                    'phase': 'pc2_channel_parse',
                },
            )
            return result
        except Exception as e:
            logger.debug("PC2 channel parse failed (%s); skipping passthrough", type(e).__name__)
            return result

        channel = None
        for child in list(root) if root is not None else []:
            tag = getattr(child, "tag", "")
            if isinstance(tag, str) and (tag == "channel" or tag.endswith("}channel")):
                channel = child
                break
        if channel is None:
            return result

        for elem in channel:
            if not _is_podcast_element(elem):
                continue
            local = _podcast_localname(elem)
            if local == "locked":
                result["locked"] = elem
                continue
            if local in _PC2_CHANNEL_STRIP:
                continue
            if local == "txt":
                purpose = elem.get("purpose", "")
                if purpose in _PC2_TXT_STRIP_PURPOSES:
                    continue
                result["passthrough"].append(elem)
                continue
            if local in _PC2_CHANNEL_PASSTHROUGH:
                result["passthrough"].append(elem)
            # Unknown podcast:* localnames at channel level: skip. Passing
            # through unknown elements would re-introduce the same lying-
            # about-cut-audio risk this whole module exists to avoid.

        return result

    def _serialize_namespaced_element(self, elem, emit_prefix: str, ns_uri: str, _depth: int = 0) -> str:
        # Generic recursive serializer used for both iTunes (single URI) and
        # any single-namespace child. Recurses only into children sharing
        # ``ns_uri`` so unrelated namespaces cannot leak through. Mirrors the
        # depth cap on ``_serialize_podcast_element``: hostile feeds with
        # deeply nested elements cannot blow Python's stack.
        if _depth > 16:
            return ""

        tag = elem.tag
        if isinstance(tag, str) and tag.startswith("{"):
            local = tag.split("}", 1)[1]
        else:
            local = tag if isinstance(tag, str) else ""

        attr_parts = []
        for attr_name, attr_value in elem.attrib.items():
            if attr_name.startswith("{"):
                continue
            attr_parts.append(f'{attr_name}="{self._escape_xml(attr_value)}"')
        attr_str = (" " + " ".join(attr_parts)) if attr_parts else ""

        child_xml = []
        ns_prefix_match = "{" + ns_uri + "}" if ns_uri else ""
        for child in elem:
            child_tag = getattr(child, "tag", "")
            if not isinstance(child_tag, str):
                continue
            if ns_uri:
                if not child_tag.startswith(ns_prefix_match):
                    continue
            else:
                if child_tag.startswith("{"):
                    continue
            child_xml.append(self._serialize_namespaced_element(child, emit_prefix, ns_uri, _depth + 1))

        raw_text = elem.text or ""
        text_xml = self._escape_xml(raw_text) if raw_text.strip() else ""

        if not child_xml and not text_xml:
            return f'<{emit_prefix}{local}{attr_str} />'
        return f'<{emit_prefix}{local}{attr_str}>{text_xml}{"".join(child_xml)}</{emit_prefix}{local}>'

    def _emit_channel_metadata_passthrough(self, lines: list, feed_content: str) -> None:
        # Pass through standard RSS and iTunes channel-level metadata from
        # upstream. Apple Podcasts and most podcast apps require several of
        # the iTunes tags (author/category/explicit/owner) to ingest a feed
        # at all -- without them, even valid artwork won't render in apps
        # because the feed is silently dropped from the directory. Also
        # emits ``<lastBuildDate>`` (regenerated by us since we just re-cut)
        # and ``<generator>`` (identifies the proxy).
        if not feed_content:
            return
        try:
            payload = feed_content.encode("utf-8") if isinstance(feed_content, str) else feed_content
            root = defused_fromstring(payload)
        except Exception:
            root = None

        channel = None
        if root is not None:
            for child in list(root):
                tag = getattr(child, "tag", "")
                if isinstance(tag, str) and (tag == "channel" or tag.endswith("}channel")):
                    channel = child
                    break

        if channel is not None:
            itunes_prefix = "{" + _ITUNES_NS + "}"
            for elem in channel:
                tag = getattr(elem, "tag", "")
                if not isinstance(tag, str):
                    continue
                if tag.startswith(itunes_prefix):
                    local = tag[len(itunes_prefix):]
                    if local in _ITUNES_CHANNEL_PASSTHROUGH:
                        lines.append(self._serialize_namespaced_element(elem, "itunes:", _ITUNES_NS))
                elif not tag.startswith("{") and tag in _RSS_CHANNEL_PASSTHROUGH:
                    lines.append(self._serialize_namespaced_element(elem, "", ""))

        # Always-emit channel timestamps and identifier so apps that rely on
        # these for refresh detection or attribution see fresh values from
        # the proxy, not stale ones inherited from upstream.
        lines.append(f'<lastBuildDate>{format_datetime(datetime.now(timezone.utc))}</lastBuildDate>')
        lines.append('<generator>MinusPod</generator>')

    def _emit_channel_pc2_tags(self, lines: list, feed_content: str, slug: str) -> None:
        # Emission order matches docs/podcasting-2.0.md: minted guid first,
        # then locked (upstream or default "yes"), then the passthrough set,
        # then the ai-content disclosure last.
        #
        # ``rstrip('/')`` keeps the GUID stable across base-URL configurations
        # that may or may not include a trailing slash; without it, toggling
        # the slash would silently change every feed's identity.
        served_feed_url = f"{self._resolved_base_url().rstrip('/')}/{slug}"
        guid = compute_feed_guid(served_feed_url)
        if guid:
            lines.append(f'<podcast:guid>{self._escape_xml(guid)}</podcast:guid>')

        upstream = self._parse_upstream_channel_pc2_tags(feed_content)

        upstream_locked_value = ""
        if upstream["locked"] is not None:
            upstream_locked_value = (upstream["locked"].text or "").strip().lower()
        if upstream_locked_value in ("yes", "no"):
            lines.append(self._serialize_podcast_element(upstream["locked"]))
        else:
            # Upstream silent, self-closing, or non-conformant body: spec says
            # locked text MUST be "yes" or "no", so fall back to default.
            lines.append('<podcast:locked>yes</podcast:locked>')

        for elem in upstream["passthrough"]:
            lines.append(self._serialize_podcast_element(elem))

        lines.append('<podcast:txt purpose="ai-content">true</podcast:txt>')

    def deduplicate_episodes(self, episodes: List[Dict]) -> List[Dict]:
        """
        De-duplicate episodes, keeping only the latest version of each.

        Duplicates are identified by matching title (normalized) and
        published date (same day). When duplicates exist, keep the one
        with the most recent published timestamp or latest URL update.

        This matches podcast app behavior which typically shows only
        the latest version when an episode is updated.

        Args:
            episodes: List of episode dicts from extract_episodes()

        Returns:
            De-duplicated list with only the latest version of each episode
        """
        if not episodes:
            return episodes

        # Group episodes by normalized title + publish date
        groups: Dict[tuple, List[Dict]] = {}
        for ep in episodes:
            # Normalize title: lowercase, strip whitespace
            title_key = (ep.get('title') or '').lower().strip()

            # Extract date portion only (ignore time for grouping)
            pub_str = ep.get('published', '')
            try:
                pub_dt = parsedate_to_datetime(pub_str)
                date_key = pub_dt.strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                date_key = pub_str[:10] if pub_str else 'unknown'

            key = (title_key, date_key)

            if key not in groups:
                groups[key] = []
            groups[key].append(ep)

        # For each group, keep only the latest version
        deduplicated = []
        for key, group in groups.items():
            if len(group) == 1:
                deduplicated.append(group[0])
            else:
                # Sort by published timestamp (most recent first)
                # Then by URL (to handle ?updated= params - higher = newer)
                def sort_key(ep):
                    try:
                        pub_dt = parsedate_to_datetime(ep.get('published', ''))
                        pub_ts = pub_dt.timestamp()
                    except (ValueError, TypeError):
                        pub_ts = 0
                    url = ep.get('url', '')
                    return (pub_ts, url)

                group.sort(key=sort_key, reverse=True)
                latest = group[0]

                logger.info(
                    f"De-duplicated {len(group)} versions of "
                    f"'{key[0][:50]}' ({key[1]}) - keeping latest"
                )
                deduplicated.append(latest)

        if len(deduplicated) < len(episodes):
            logger.info(
                f"Removed {len(episodes) - len(deduplicated)} duplicate episodes"
            )

        return deduplicated

    def extract_episodes(self, feed_content: str, parsed_feed=None) -> List[Dict]:
        """Extract episode information from feed.

        Args:
            feed_content: Original RSS feed XML.
            parsed_feed: Optional pre-parsed feedparser object. When supplied,
                skips the internal parse_feed call so a single refresh cycle
                does not pay the parse cost three times.
        """
        feed = parsed_feed if parsed_feed is not None else self.parse_feed(feed_content)
        if not feed:
            return []

        episodes = []
        for entry in feed.entries:
            episode_url = None
            for enclosure in entry.get('enclosures', []):
                if 'audio' in enclosure.get('type', ''):
                    episode_url = enclosure.get('href', '')
                    break

            if episode_url:
                # Extract episode artwork (itunes:image or standard image tag)
                artwork_url = None
                if hasattr(entry, 'image') and hasattr(entry.image, 'href'):
                    artwork_url = entry.image.href
                elif 'itunes_image' in entry:
                    artwork_url = entry.itunes_image.get('href')

                # Extract episode number (itunes:episode)
                episode_number = None
                if hasattr(entry, 'itunes_episode'):
                    try:
                        episode_number = int(entry.itunes_episode)
                    except (ValueError, TypeError):
                        pass

                # Map per-episode iTunes categories to vocabulary tags.
                ep_tags: List[str] = []
                try:
                    from utils.community_tags import map_itunes_category
                    raw_cats = self.extract_episode_categories(entry)
                    seen = set()
                    for cat in raw_cats:
                        tag = map_itunes_category(cat)
                        if tag and tag not in seen:
                            seen.add(tag)
                            ep_tags.append(tag)
                except Exception as e:
                    logger.warning(f"Episode iTunes category mapping failed: {e}")

                episodes.append({
                    'id': self.generate_episode_id(episode_url, entry.get('id', '')),
                    'url': episode_url,
                    'title': entry.get('title', 'Unknown'),
                    'published': entry.get('published', ''),
                    'description': self._get_episode_description(entry),
                    'artwork_url': artwork_url,
                    'episode_number': episode_number,
                    'tags': ep_tags,
                })

        # De-duplicate episodes (keep latest when multiple versions exist)
        return self.deduplicate_episodes(episodes)