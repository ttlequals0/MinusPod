"""Served RSS for a YouTube feed: processed episodes rendered by the local feed builder."""
import logging

from utils.time import utc_now_iso

logger = logging.getLogger(__name__)


def source_link(podcast: dict) -> str:
    if podcast.get('youtube_source_kind') == 'channel':
        return f"https://www.youtube.com/channel/{podcast.get('youtube_source_id')}"
    return f"https://www.youtube.com/playlist?list={podcast.get('youtube_source_id')}"


def rebuild_youtube_feed(slug: str, podcast: dict | None = None) -> bool:
    # lazy: local_feed_builder and main_app import main_app at import time
    from local_feed_builder import build_local_feed_xml
    from main_app import db, storage
    from main_app.shared_state import invalidate_episode_lookup_cache
    podcast = podcast or db.get_podcast_by_slug(slug)
    if not podcast:
        return False
    try:
        cap = db.get_max_episodes_for_podcast(slug, podcast=podcast)
        episodes = db.get_youtube_feed_episodes_for_rss(podcast['id'], cap)
        xml = build_local_feed_xml(podcast, episodes, storage=storage, db=db, channel_link=source_link(podcast))
        storage.save_rss(slug, xml)
        db.update_podcast(slug, last_checked_at=utc_now_iso())
        invalidate_episode_lookup_cache(slug)
        return True
    except Exception as exc:
        logger.warning(f"[{slug}] YouTube feed RSS rebuild failed: {exc}")
        return False
