"""Podcast CRUD mixin for MinusPod database."""
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class PodcastMixin:
    """Podcast management methods."""

    def get_all_podcasts(self) -> List[Dict]:
        """Get all podcasts with episode counts."""
        conn = self.get_connection()
        cursor = conn.execute("""
            SELECT p.*,
                   COUNT(e.id) as episode_count,
                   SUM(CASE WHEN e.status = 'processed' THEN 1 ELSE 0 END) as processed_count,
                   MAX(e.created_at) as last_episode_date
            FROM podcasts p
            LEFT JOIN episodes e ON p.id = e.podcast_id
            GROUP BY p.id
            ORDER BY p.created_at DESC
        """)
        return [dict(row) for row in cursor.fetchall()]

    def get_podcast_by_slug(self, slug: str) -> Optional[Dict]:
        """Get podcast by slug with episode counts."""
        conn = self.get_connection()
        cursor = conn.execute("""
            SELECT p.*,
                   COUNT(e.id) as episode_count,
                   SUM(CASE WHEN e.status = 'processed' THEN 1 ELSE 0 END) as processed_count,
                   MAX(e.created_at) as last_episode_date
            FROM podcasts p
            LEFT JOIN episodes e ON p.id = e.podcast_id
            WHERE p.slug = ?
            GROUP BY p.id
        """, (slug,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_podcast(self, slug: str, source_url: str, title: str = None) -> int:
        """Create a new podcast. Returns podcast ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)""",
            (slug, source_url, title)
        )
        conn.commit()
        return cursor.lastrowid

    def update_podcast(self, slug: str, **kwargs) -> bool:
        """Update podcast fields."""
        if not kwargs:
            return False

        conn = self.get_connection()

        # Build update query
        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('title', 'description', 'artwork_url', 'artwork_cached',
                       'last_checked_at', 'source_url', 'network_id', 'dai_platform',
                       'network_id_override', 'audio_analysis_override', 'auto_process_override',
                       'max_episodes', 'etag', 'last_modified_header',
                       'only_expose_processed_episodes'):
                fields.append(f"{key} = ?")
                values.append(value)

        if not fields:
            return False

        fields.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
        values.append(slug)

        conn.execute(
            f"UPDATE podcasts SET {', '.join(fields)} WHERE slug = ?",
            values
        )
        conn.commit()
        return True

    def delete_podcast(self, slug: str) -> bool:
        """Delete podcast and all associated data."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM podcasts WHERE slug = ?", (slug,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def update_podcast_etag(self, slug: str, etag: str, last_modified: str) -> bool:
        """Update ETag and Last-Modified header for conditional GET support.

        Args:
            slug: Podcast slug
            etag: ETag header value from RSS server
            last_modified: Last-Modified header value from RSS server

        Returns:
            True if update succeeded
        """
        return self.update_podcast(slug, etag=etag, last_modified_header=last_modified)

    # ---- Per-feed -> global default resolvers ----

    DEFAULT_MAX_FEED_EPISODES = 300

    def get_max_episodes_for_podcast(self, slug: str,
                                     podcast: Optional[Dict] = None) -> int:
        """Resolve max_episodes for a podcast: per-feed value if set, else
        the max_feed_episodes global setting, else DEFAULT_MAX_FEED_EPISODES.
        """
        if podcast is None:
            podcast = self.get_podcast_by_slug(slug)
        per_feed = podcast.get('max_episodes') if podcast else None
        if per_feed:
            return int(per_feed)
        global_value = self.get_setting('max_feed_episodes')
        if global_value:
            try:
                return int(global_value)
            except (TypeError, ValueError):
                pass
        return self.DEFAULT_MAX_FEED_EPISODES

    def is_only_expose_processed_for_podcast(self, slug: str,
                                             podcast: Optional[Dict] = None) -> bool:
        """Resolve only_expose_processed_episodes for a podcast: per-feed
        value if non-NULL (0=off, 1=on), else the
        only_expose_processed_default global setting, else False.
        """
        if podcast is None:
            podcast = self.get_podcast_by_slug(slug)
        per_feed = podcast.get('only_expose_processed_episodes') if podcast else None
        if per_feed is not None:
            return bool(per_feed)
        return self.get_setting('only_expose_processed_default') == 'true'
