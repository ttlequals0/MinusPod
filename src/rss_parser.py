"""RSS feed parsing and management."""
import feedparser
import logging
import hashlib
import os
from datetime import datetime
from typing import Dict, List, Optional
import requests
from slugify import slugify

logger = logging.getLogger(__name__)

class RSSParser:
    def __init__(self, base_url: str = None):
        self.base_url = base_url or os.getenv('BASE_URL', 'http://localhost:8000')

    def fetch_feed(self, url: str, timeout: int = 30) -> Optional[str]:
        """Fetch RSS feed from URL."""
        try:
            logger.info(f"Fetching RSS feed from: {url}")
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            logger.info(f"Successfully fetched RSS feed, size: {len(response.content)} bytes")
            return response.text
        except requests.exceptions.ContentDecodingError as e:
            # Some servers claim gzip encoding but send malformed data
            # Retry without accepting compressed responses
            logger.warning(f"Gzip decompression failed, retrying without compression: {e}")
            try:
                headers = {'Accept-Encoding': 'identity'}
                response = requests.get(url, timeout=timeout, headers=headers)
                response.raise_for_status()
                logger.info(f"Successfully fetched RSS feed (uncompressed), size: {len(response.content)} bytes")
                return response.text
            except requests.RequestException as retry_e:
                logger.error(f"Failed to fetch RSS feed (retry): {retry_e}")
                return None
        except requests.RequestException as e:
            logger.error(f"Failed to fetch RSS feed: {e}")
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
        headers = {'User-Agent': 'PodcastAdRemover/1.0'}

        if etag:
            headers['If-None-Match'] = etag
        if last_modified:
            headers['If-Modified-Since'] = last_modified

        try:
            response = requests.get(url, headers=headers, timeout=timeout)

            if response.status_code == 304:
                logger.info(f"Feed not modified (304): {url}")
                return None, etag, last_modified

            response.raise_for_status()

            new_etag = response.headers.get('ETag')
            new_last_modified = response.headers.get('Last-Modified')

            logger.info(f"Fetched RSS feed, size: {len(response.content)} bytes")
            return response.text, new_etag, new_last_modified

        except requests.exceptions.ContentDecodingError as e:
            # Retry without accepting compressed responses
            logger.warning(f"Gzip decompression failed, retrying: {e}")
            try:
                headers['Accept-Encoding'] = 'identity'
                response = requests.get(url, headers=headers, timeout=timeout)
                if response.status_code == 304:
                    return None, etag, last_modified
                response.raise_for_status()
                return (
                    response.text,
                    response.headers.get('ETag'),
                    response.headers.get('Last-Modified')
                )
            except requests.RequestException:
                return None, None, None

        except requests.RequestException as e:
            logger.error(f"Conditional fetch failed: {e}")
            return None, None, None

    def parse_feed(self, feed_content: str) -> Dict:
        """Parse RSS feed content."""
        try:
            feed = feedparser.parse(feed_content)
            if feed.bozo:
                logger.warning(f"RSS parse warning: {feed.bozo_exception}")

            logger.info(f"Parsed RSS feed: {feed.feed.get('title', 'Unknown')} with {len(feed.entries)} entries")
            return feed
        except Exception as e:
            logger.error(f"Failed to parse RSS feed: {e}")
            return None

    def generate_episode_id(self, episode_url: str) -> str:
        """Generate consistent episode ID from URL."""
        # Use MD5 hash of URL for consistent ID
        return hashlib.md5(episode_url.encode()).hexdigest()[:12]

    def modify_feed(self, feed_content: str, slug: str) -> str:
        """Modify RSS feed to use our server URLs."""
        feed = self.parse_feed(feed_content)
        if not feed:
            return feed_content

        # Build modified RSS
        lines = []
        lines.append('<?xml version="1.0" encoding="UTF-8"?>')
        lines.append('<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">')
        lines.append('<channel>')

        # Copy channel metadata
        channel = feed.feed
        lines.append(f'<title>{channel.get("title", "")}</title>')
        lines.append(f'<link>{channel.get("link", "")}</link>')
        lines.append(f'<description><![CDATA[{channel.get("description", "")}]]></description>')
        lines.append(f'<language>{channel.get("language", "en")}</language>')

        if 'image' in channel:
            lines.append(f'<image>')
            lines.append(f'  <url>{channel.image.get("href", "")}</url>')
            lines.append(f'  <title>{channel.image.get("title", "")}</title>')
            lines.append(f'  <link>{channel.image.get("link", "")}</link>')
            lines.append(f'</image>')

        # Limit to most recent episodes to keep feed size manageable
        # Pocket Casts and other apps may reject very large feeds (>1MB)
        max_episodes = 100
        entries = feed.entries[:max_episodes]

        if len(feed.entries) > max_episodes:
            logger.info(f"[{slug}] Limiting feed from {len(feed.entries)} to {max_episodes} episodes")

        # Process each episode
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

            episode_id = self.generate_episode_id(episode_url)
            modified_url = f"{self.base_url}/episodes/{slug}/{episode_id}.mp3"

            lines.append('<item>')
            lines.append(f'  <title>{self._escape_xml(entry.get("title", ""))}</title>')
            lines.append(f'  <description><![CDATA[{entry.get("description", "")}]]></description>')
            lines.append(f'  <link>{entry.get("link", "")}</link>')
            lines.append(f'  <guid>{entry.get("id", episode_url)}</guid>')
            lines.append(f'  <pubDate>{entry.get("published", "")}</pubDate>')

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

            # Episode artwork (itunes:image)
            artwork_url = None
            if hasattr(entry, 'image') and hasattr(entry.image, 'href'):
                artwork_url = entry.image.href
            elif 'itunes_image' in entry:
                artwork_url = entry.itunes_image.get('href')
            if artwork_url:
                lines.append(f'  <itunes:image href="{self._escape_xml(artwork_url)}" />')

            lines.append('</item>')

        lines.append('</channel>')
        lines.append('</rss>')

        modified_rss = '\n'.join(lines)
        logger.info(f"[{slug}] Modified RSS feed with {len(entries)} episodes")
        return modified_rss

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

                episodes.append({
                    'id': self.generate_episode_id(episode_url),
                    'url': episode_url,
                    'title': entry.get('title', 'Unknown'),
                    'published': entry.get('published', ''),
                    'description': entry.get('description', ''),
                    'artwork_url': artwork_url,
                })

        return episodes