"""The combined recents feed (#721): one row, no episodes of its own, served
from a cross-feed membership query with every item under its source slug."""
import logging

from config import resolve_chapters_in_notes
from database.podcasts import RECENTS_SLUG, is_recents_feed, recents_cutoff
from local_feed_builder import _PODCAST_NS, _append_local_episode_item, _channel_artwork_url
from main_app import db, rss_parser, storage
from main_app.feed_auth import active_feed_key
from main_app.shared_state import invalidate_episode_lookup_cache
from utils.feed_guid import compute_feed_guid
from utils.time import utc_now_iso

logger = logging.getLogger('podcast.refresh')


def build_recents_feed_xml(podcast: dict, episodes: list[dict], *, storage, db) -> str:
    base = rss_parser._resolved_base_url()
    feed_auth_key = active_feed_key(db)
    list_chapters = resolve_chapters_in_notes(db, None)
    title = podcast.get('title') or 'Recents'
    channel_link = f"{base}/{RECENTS_SLUG}"

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
             f'xmlns:podcast="{_PODCAST_NS}">',
             '<channel>',
             f'<title>{rss_parser._escape_xml(title)}</title>',
             f'<link>{rss_parser._escape_xml(channel_link)}</link>',
             f'<description><![CDATA[{rss_parser._escape_cdata(podcast.get("description") or "")}]]></description>',
             '<language>en</language>',
             f'<lastBuildDate>{rss_parser._format_rfc2822(utc_now_iso())}</lastBuildDate>',
             '<generator>MinusPod</generator>']
    artwork_url = _channel_artwork_url(RECENTS_SLUG, base, feed_auth_key, storage)
    if artwork_url:
        lines += ['<image>', f'  <url>{rss_parser._escape_xml(artwork_url)}</url>',
                  f'  <title>{rss_parser._escape_xml(title)}</title>',
                  f'  <link>{rss_parser._escape_xml(channel_link)}</link>', '</image>',
                  f'<itunes:image href="{rss_parser._escape_xml(artwork_url)}" />']
    guid = compute_feed_guid(f"{base.rstrip('/')}/{RECENTS_SLUG}")
    lines.append(f'<podcast:guid>{rss_parser._escape_xml(guid)}</podcast:guid>')
    lines.append('<podcast:locked>yes</podcast:locked>')
    lines.append('<podcast:medium>podcast</podcast:medium>')
    lines.append('<podcast:txt purpose="ai-content">true</podcast:txt>')

    for ep in episodes:
        # Per-item map: episode ids are only unique within a feed.
        notes = {ep['episode_id']: ep['chapters_json']} if list_chapters else {}
        _append_local_episode_item(lines, ep['source_slug'], ep, base, storage, feed_auth_key,
                                   chapter_notes=notes, title_prefix=f"{ep['source_title']}: ")
    lines += ['</channel>', '</rss>']
    return '\n'.join(lines)


def rebuild_recents_feed(podcast: dict | None = None) -> bool:
    """Render and cache /recents; False when no recents row exists."""
    podcast = podcast or db.get_podcast_by_slug(RECENTS_SLUG)
    if not is_recents_feed(podcast):
        return False
    try:
        cap = db.get_max_episodes_for_podcast(RECENTS_SLUG, podcast=podcast)
        episodes = db.get_recent_processed_episodes(recents_cutoff(podcast), limit=cap)
        storage.save_rss(RECENTS_SLUG, build_recents_feed_xml(podcast, episodes, storage=storage, db=db))
        db.update_podcast(RECENTS_SLUG, last_checked_at=utc_now_iso())
        invalidate_episode_lookup_cache(RECENTS_SLUG)
        return True
    except Exception as e:
        logger.warning(f"[{RECENTS_SLUG}] rebuild failed: {e}")
        return False
