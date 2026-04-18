"""RSS feed parsing and management."""
import feedparser
import logging
import hashlib
import os
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from typing import Dict, List, Optional
import requests

from urllib.parse import urlparse

from config import APP_USER_AGENT, HTTP_MAX_REDIRECTS_FEED
from utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
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


class RSSParser:
    def __init__(self, base_url: str = None):
        self.base_url = base_url or os.getenv('BASE_URL', 'http://localhost:8000')

    def fetch_feed(self, url: str, timeout: int = 30) -> Optional[str]:
        """Fetch RSS feed from URL."""
        try:
            _get_rss_circuit_breaker(url).check()
        except CircuitBreakerOpen as e:
            logger.debug(f"RSS fetch skipped: {e}")
            return None

        try:
            logger.info(f"Fetching RSS feed from: {safe_url_for_log(url)}")
            response = safe_get(
                url,
                trust=URLTrust.OPERATOR_CONFIGURED,
                timeout=timeout,
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                stream=True,
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
                    trust=URLTrust.OPERATOR_CONFIGURED,
                    timeout=timeout,
                    max_redirects=HTTP_MAX_REDIRECTS_FEED,
                    headers={'Accept-Encoding': 'identity'},
                )
                response.raise_for_status()
                logger.info(f"Successfully fetched RSS feed (uncompressed), size: {len(response.content)} bytes")
                _get_rss_circuit_breaker(url).record_success()
                return response.text
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
                trust=URLTrust.OPERATOR_CONFIGURED,
                timeout=timeout,
                max_redirects=HTTP_MAX_REDIRECTS_FEED,
                headers=headers,
            )

            if response.status_code == 304:
                logger.info(f"Feed not modified (304): {safe_url_for_log(url)}")
                _get_rss_circuit_breaker(url).record_success()
                return None, etag, last_modified

            response.raise_for_status()

            new_etag = response.headers.get('ETag')
            new_last_modified = response.headers.get('Last-Modified')

            logger.info(f"Fetched RSS feed, size: {len(response.content)} bytes")
            _get_rss_circuit_breaker(url).record_success()
            return response.text, new_etag, new_last_modified

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
                    trust=URLTrust.OPERATOR_CONFIGURED,
                    timeout=timeout,
                    max_redirects=HTTP_MAX_REDIRECTS_FEED,
                    headers=headers,
                )
                if response.status_code == 304:
                    _get_rss_circuit_breaker(url).record_success()
                    return None, etag, last_modified
                response.raise_for_status()
                _get_rss_circuit_breaker(url).record_success()
                return (
                    response.text,
                    response.headers.get('ETag'),
                    response.headers.get('Last-Modified')
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
            # Only scan the first 4 KB; legitimate feeds declare their
            # prolog up front, and this keeps the cost bounded.
            header = header_bytes[:4096].lower()
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

            logger.info(f"Parsed RSS feed: {feed.feed.get('title', 'Unknown')} with {len(feed.entries)} entries")
            return feed
        except Exception as e:
            logger.error(f"Failed to parse RSS feed: {e}")
            return None

    @staticmethod
    def extract_podcast_artwork_url(parsed_feed) -> Optional[str]:
        """Extract podcast-level artwork URL from a parsed feed."""
        if not parsed_feed or not parsed_feed.feed:
            return None
        feed = parsed_feed.feed
        if hasattr(feed, 'image') and hasattr(feed.image, 'href'):
            return feed.image.href
        # Fallback to itunes:image
        if 'itunes_image' in feed:
            return feed.itunes_image.get('href')
        return None

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
                    extra_episodes: Optional[List[Dict]] = None) -> str:
        """Modify RSS feed to use our server URLs.

        Args:
            feed_content: Original RSS feed XML
            slug: Podcast slug
            storage: Optional Storage instance for checking Podcasting 2.0 assets
            max_episodes: Max episodes to include in feed (1-500, default 300)
            extra_episodes: Processed episodes from DB to append beyond the cap.
                Each dict must have: episode_id, title, description, published_at,
                new_duration, episode_number.
        """
        feed = self.parse_feed(feed_content)
        if not feed:
            return feed_content

        # Build modified RSS with Podcasting 2.0 namespace
        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append('<rss version="2.0" '
                     'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
                     'xmlns:podcast="https://podcastindex.org/namespace/1.0">')
        lines.append('<channel>')

        # Copy channel metadata (escape XML entities to prevent invalid XML from & in URLs)
        channel = feed.feed
        lines.append(f'<title>{self._escape_xml(channel.get("title", ""))}</title>')
        lines.append(f'<link>{self._escape_xml(channel.get("link", ""))}</link>')
        lines.append(f'<description><![CDATA[{channel.get("description", "")}]]></description>')
        lines.append(f'<language>{self._escape_xml(channel.get("language", "en"))}</language>')

        if 'image' in channel:
            lines.append(f'<image>')
            lines.append(f'  <url>{self._escape_xml(channel.image.get("href", ""))}</url>')
            lines.append(f'  <title>{self._escape_xml(channel.image.get("title", ""))}</title>')
            lines.append(f'  <link>{self._escape_xml(channel.image.get("link", ""))}</link>')
            lines.append(f'</image>')

        # Limit to most recent episodes to keep feed size manageable
        # Pocket Casts and other apps may reject very large feeds (>1MB)
        max_episodes = max(1, min(max_episodes, 500))
        entries = feed.entries[:max_episodes]

        if len(feed.entries) > max_episodes:
            logger.info(f"[{slug}] Limiting feed from {len(feed.entries)} to {max_episodes} episodes")

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
            included_episode_ids.add(episode_id)
            modified_url = f"{self.base_url}/episodes/{slug}/{episode_id}.mp3"

            lines.append('<item>')
            lines.append(f'  <title>{self._escape_xml(entry.get("title", ""))}</title>')
            lines.append(f'  <description><![CDATA[{self._get_episode_description(entry)}]]></description>')
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

            # Podcasting 2.0 tags (transcript and chapters)
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

        total_episodes = len(entries) + appended_count
        modified_rss = '\n'.join(lines)
        logger.info(f"[{slug}] Modified RSS feed with {total_episodes} episodes ({appended_count} appended from DB)")
        return modified_rss

    def _append_podcasting2_tags(self, lines: list, slug: str, episode_id: str, storage) -> None:
        """Append Podcasting 2.0 transcript and chapters tags if available."""
        if not storage:
            return
        if storage.has_transcript_vtt(slug, episode_id):
            transcript_url = f"{self.base_url}/episodes/{slug}/{episode_id}.vtt"
            lines.append(f'  <podcast:transcript url="{transcript_url}" type="text/vtt" language="en" rel="captions" />')
        if storage.has_chapters_json(slug, episode_id):
            chapters_url = f"{self.base_url}/episodes/{slug}/{episode_id}/chapters.json"
            lines.append(f'  <podcast:chapters url="{chapters_url}" type="application/json+chapters" />')

    def _append_db_episode_item(self, lines: list, slug: str, ep: Dict, storage) -> None:
        """Append a single <item> for a processed episode from the database."""
        ep_id = ep['episode_id']
        modified_url = f"{self.base_url}/episodes/{slug}/{ep_id}.mp3"
        lines.append('<item>')
        lines.append(f'  <title>{self._escape_xml(ep.get("title") or "Unknown")}</title>')
        if ep.get('description'):
            lines.append(f'  <description><![CDATA[{ep["description"]}]]></description>')
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

    def extract_episodes(self, feed_content: str) -> List[Dict]:
        """Extract episode information from feed."""
        feed = self.parse_feed(feed_content)
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

                episodes.append({
                    'id': self.generate_episode_id(episode_url, entry.get('id', '')),
                    'url': episode_url,
                    'title': entry.get('title', 'Unknown'),
                    'published': entry.get('published', ''),
                    'description': self._get_episode_description(entry),
                    'artwork_url': artwork_url,
                    'episode_number': episode_number,
                })

        # De-duplicate episodes (keep latest when multiple versions exist)
        return self.deduplicate_episodes(episodes)