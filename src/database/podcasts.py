"""Podcast CRUD mixin for MinusPod database."""
import json
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
            elif key in ('tags', 'user_tags'):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if isinstance(value, list) else value)

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

    def get_podcast_tags(self, slug: str) -> Dict[str, List[str]]:
        """Return the source breakdown of a podcast's tags.

        Returns {'effective': [...], 'rss': [...], 'episode': [...], 'user': [...]}.
        `rss` is derived as (tags - user_tags - episode_tags) and may include
        any RSS-extracted tag from past parses.
        """
        conn = self.get_connection()
        row = conn.execute(
            "SELECT id, tags, user_tags FROM podcasts WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return {'effective': [], 'rss': [], 'episode': [], 'user': []}
        try:
            effective = json.loads(row['tags'] or '[]') or []
        except (ValueError, TypeError):
            effective = []
        try:
            user = json.loads(row['user_tags'] or '[]') or []
        except (ValueError, TypeError):
            user = []
        # episode-level tags: union across episodes of this podcast
        episode_tags: set = set()
        cur = conn.execute(
            "SELECT tags FROM episodes WHERE podcast_id = ?", (row['id'],)
        )
        for ep in cur.fetchall():
            try:
                tags = json.loads(ep['tags'] or '[]') or []
            except (ValueError, TypeError):
                tags = []
            if isinstance(tags, list):
                episode_tags.update(tags)

        user_set = set(user)
        ep_set = set(episode_tags)
        rss = [t for t in effective if t not in user_set and t not in ep_set]
        return {
            'effective': effective,
            'rss': rss,
            'episode': sorted(episode_tags),
            'user': user,
        }

    def set_podcast_tags(self, slug: str, *, rss_tags: List[str] = None,
                         user_tags: List[str] = None) -> bool:
        """Recompute and persist podcasts.tags as union of provided + episode tags.

        Pass `rss_tags` to update the RSS-derived layer (caller decides which
        subset is RSS-only), or `user_tags` for the user-mutable layer. The
        denormalized `tags` field is rewritten to the union of (rss_tags, the
        existing user_tags or the override, and all episodes.tags for this podcast).

        Fast pre-check: when `rss_tags` is provided and is already a subset of
        the row's current `tags` AND `user_tags` is not changing, skip the
        episode-aggregation pass entirely. This is the dominant case on the
        feed-refresh hot path — a 300-episode podcast paid one SELECT + 300
        JSON parses every 15 minutes for nothing.
        """
        conn = self.get_connection()
        row = conn.execute(
            "SELECT id, tags, user_tags FROM podcasts WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return False

        try:
            current_user = json.loads(row['user_tags'] or '[]') or []
        except (ValueError, TypeError):
            current_user = []
        try:
            current_all = set(json.loads(row['tags'] or '[]') or [])
        except (ValueError, TypeError):
            current_all = set()

        # Fast path: RSS-only update where the incoming set is already covered
        # by the existing union and the user layer isn't being touched. The
        # episode-level aggregation can only grow the union, so if the RSS
        # tags are already present we know the final union won't shrink.
        if (
            user_tags is None
            and rss_tags is not None
            and set(rss_tags).issubset(current_all)
        ):
            return True

        effective_user = list(user_tags) if user_tags is not None else current_user

        # Pull episode-level tags. Done as one SELECT + JSON parse per row;
        # podcasts with hundreds of episodes pay this cost only when we
        # actually need to recompute the denormalized union.
        episode_tags: set = set()
        cur = conn.execute(
            "SELECT tags FROM episodes WHERE podcast_id = ?", (row['id'],)
        )
        for ep in cur.fetchall():
            try:
                tags = json.loads(ep['tags'] or '[]') or []
            except (ValueError, TypeError):
                tags = []
            if isinstance(tags, list):
                episode_tags.update(tags)

        if rss_tags is not None:
            effective_rss = set(rss_tags)
        else:
            effective_rss = current_all - set(current_user) - episode_tags

        union = sorted(set(effective_user) | effective_rss | episode_tags)
        if set(union) == current_all and effective_user == current_user:
            return True
        conn.execute(
            "UPDATE podcasts SET tags = ?, user_tags = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE slug = ?",
            (json.dumps(union), json.dumps(effective_user), slug),
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
