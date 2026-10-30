"""Served RSS for a YouTube feed: discovered and processed episodes rendered by the local feed builder."""
import logging

from utils.time import utc_now_iso
from youtube.urls import Source, source_url

logger = logging.getLogger(__name__)


def source_link(podcast: dict) -> str:
    return source_url(Source(podcast.get('youtube_source_kind'), podcast.get('youtube_source_id')))


def rebuild_youtube_feed(slug: str, podcast: dict | None = None) -> bool:
    # lazy: local_feed_builder and main_app import main_app at import time
    from local_feed_builder import _fetch_local_feed_episodes, build_local_feed_xml
    from main_app import db, storage
    from main_app.shared_state import invalidate_episode_lookup_cache
    podcast = podcast or db.get_podcast_by_slug(slug)
    if not podcast:
        return False
    try:
        cap = db.get_max_episodes_for_podcast(slug, podcast=podcast)
        processed_only = db.is_only_expose_processed_for_podcast(slug, podcast=podcast)
        episodes = _fetch_local_feed_episodes(db, podcast['id'], cap, processed_only=processed_only)
        xml = build_local_feed_xml(podcast, episodes, storage=storage, db=db, channel_link=source_link(podcast))
        storage.save_rss(slug, xml)
        db.update_podcast(slug, last_checked_at=utc_now_iso())
        invalidate_episode_lookup_cache(slug)
        return True
    except Exception as exc:
        logger.warning(f"[{slug}] YouTube feed RSS rebuild failed: {exc}")
        return False
