"""The combined recents feed (#721): one row, no episodes of its own, served
from a cross-feed membership query with every item under its source slug."""
import logging

from audio_processor import get_replacement_duration
from config import resolve_toggle
from database.podcasts import RECENTS_SLUG, is_recents_feed, recents_cutoff
from database.settings import feed_itunes_block_enabled
import local_feed_builder
from feed_build_support import active_feed_key, db, rss_parser, storage
from lookup_cache import invalidate_episode_lookup_cache
from utils.feed_guid import compute_feed_guid
from utils.time import utc_now_iso

logger = logging.getLogger('podcast.refresh')


def build_recents_feed_xml(podcast: dict, episodes: list[dict], *, storage, db) -> str:
    base = rss_parser._resolved_base_url()
    feed_auth_key = active_feed_key(db)
    chapters_default = db.get_setting_bool('chapters_in_notes', False)
    review_default = db.get_setting_bool('review_links_in_notes', False)
    title = podcast.get('title') or 'Recents'
    channel_link = f"{base}/{RECENTS_SLUG}"
    replacement_duration = get_replacement_duration()

    lines = local_feed_builder._channel_open(title, channel_link, podcast.get('description') or '',
                          itunes_block=feed_itunes_block_enabled(db))
    local_feed_builder._append_channel_image(lines, local_feed_builder._channel_artwork_url(RECENTS_SLUG, base, feed_auth_key, storage),
                          title, channel_link)
    guid = compute_feed_guid(f"{base.rstrip('/')}/{RECENTS_SLUG}")
    lines.append(f'<podcast:guid>{rss_parser._escape_xml(guid)}</podcast:guid>')
    lines.append('<podcast:locked>yes</podcast:locked>')
    lines.append('<podcast:medium>podcast</podcast:medium>')
    lines.append('<podcast:txt purpose="ai-content">true</podcast:txt>')

    for ep in episodes:
        # Same per-feed override the source feed's own RSS honours.
        list_chapters = resolve_toggle(ep.get('source_chapters_in_notes'), chapters_default)
        # Per-item map: episode ids are only unique within a feed.
        notes = {ep['episode_id']: ep['chapters_json']} if list_chapters else {}
        cuts_json = ep.get('applied_cuts_json')
        local_feed_builder._append_local_episode_item(lines, ep['source_slug'], ep, base, storage, feed_auth_key,
                                   chapter_notes=notes, title_prefix=f"{ep['source_title']}: ",
                                   applied_cuts={ep['episode_id']: cuts_json} if cuts_json else None,
                                   replacement_duration=replacement_duration,
                                   has_transcript=bool(ep['has_transcript_vtt']),
                                   has_chapters=bool(ep['chapters_json']),
                                   review_links=resolve_toggle(
                                       ep.get('source_review_links_in_notes'), review_default))
    lines += ['</channel>', '</rss>']
    return '\n'.join(lines)


def rebuild_recents_feed(podcast: dict | None = None) -> bool:
    """Render and cache /recents; False when no recents row exists."""
    podcast = podcast or db.get_podcast_by_slug(RECENTS_SLUG)
    if not is_recents_feed(podcast):
        return False
    try:
        cap = db.get_max_episodes_for_podcast(RECENTS_SLUG, podcast=podcast)
        episodes = db.get_recent_processed_episodes(recents_cutoff(podcast), limit=cap, details=True)
        storage.save_rss(RECENTS_SLUG, build_recents_feed_xml(podcast, episodes, storage=storage, db=db))
        db.update_podcast(RECENTS_SLUG, last_checked_at=utc_now_iso())
        invalidate_episode_lookup_cache(RECENTS_SLUG)
        return True
    except Exception as e:
        logger.warning(f"[{RECENTS_SLUG}] rebuild failed: {e}")
        return False
