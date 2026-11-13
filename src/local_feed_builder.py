"""RSS builder for local (imported-archive) feeds.

A local feed (``podcasts.feed_type == 'local'``) has no upstream XML, so ``rss_parser.modify_feed``
cannot run. This module renders the served feed from the podcasts/episodes rows, reusing
``modify_feed``'s line-assembly idioms, URL shapes and namespace declarations so a local feed is
indistinguishable in shape from a subscribed one.

Podcasting 2.0 extras (funding, person, license, location, txt) are read from
``podcasts.p20_channel_json`` / ``episodes.p20_item_json`` as ``{"tag": [{"text": ..., <attr>: ...}, ...]}``,
a list per tag even for the spec-singular ``license``/``location``, so one serializer covers all five.
``podroll`` is stored the same way (``{"podroll": [{"feedGuid": ..., <attr>: ...}, ...]}``) but rendered
as a nested container of remoteItem children (see ``_emit_podroll``), having no flat text form.
"""
import json
import logging

from audio_processor import get_replacement_duration
from chapter_notes import chapter_notes_for
from config import resolve_review_links_in_notes
from database.episodes import NOT_AWAITING_APPROVAL
from database.settings import feed_itunes_block_enabled
from feed_build_support import active_feed_key, db, rss_parser, storage
from lookup_cache import invalidate_episode_lookup_cache
from rss_parser import ITUNES_BLOCK_LINE, RSS_RENDER_VERSION, content_encoded_line, effective_channel_text, item_body_html
from utils.episode_paths import episode_public_url, published_episode_version
from utils.feed_guid import compute_feed_guid
from utils.json_fields import load_json_list
from utils.time import utc_now_iso

logger = logging.getLogger('podcast.feed')

_PODCAST_NS = "https://podcastindex.org/namespace/1.0"

# Attribute whitelist per Podcasting 2.0 tag; the element body is always the
# item's "text" key. Keeps the passthrough generic instead of one bespoke
# emitter per tag.
_FUNDING_ATTRS = ('url',)
_PERSON_ATTRS = ('role', 'group', 'img', 'href')
_LICENSE_ATTRS = ('url',)
_LOCATION_ATTRS = ('geo', 'osm')
_TXT_ATTRS = ('purpose',)

# podcast:podroll is a channel-level container of self-closing remoteItem children, not a flat list
# of <podcast:{tag}> elements, so _emit_pc2_items does not cover it (see _emit_podroll). Attr order
# matches the podcastindex namespace's remoteItem example (feedGuid, feedUrl, itemGuid, medium).
_PODROLL_REMOTE_ITEM_ATTRS = ('feedGuid', 'feedUrl', 'itemGuid', 'medium')


def _load_json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _emit_pc2_items(lines: list, tag: str, items, attr_keys: tuple) -> None:
    """Append one ``<podcast:{tag} attr...>text</podcast:{tag}>`` per dict
    in ``items``. Unknown keys are ignored; non-dict entries are skipped."""
    for item in items or []:
        if not isinstance(item, dict):
            continue
        text = item.get('text') or ''
        attr_parts = []
        for key in attr_keys:
            value = item.get(key)
            if value:
                attr_parts.append(f'{key}="{rss_parser._escape_xml(str(value))}"')
        attr_str = (' ' + ' '.join(attr_parts)) if attr_parts else ''
        if text:
            lines.append(f'  <podcast:{tag}{attr_str}>{rss_parser._escape_xml(text)}</podcast:{tag}>')
        else:
            lines.append(f'  <podcast:{tag}{attr_str} />')


def _emit_podroll(lines: list, entries) -> None:
    """Append a ``<podcast:podroll>`` container wrapping one self-closing ``<podcast:remoteItem>`` per
    entry that has a feedGuid. Emits nothing without valid entries: an empty container is noise to podcast apps."""
    valid = [e for e in (entries or []) if isinstance(e, dict) and e.get('feedGuid')]
    if not valid:
        return
    lines.append('  <podcast:podroll>')
    for entry in valid:
        attr_parts = []
        for key in _PODROLL_REMOTE_ITEM_ATTRS:
            value = entry.get(key)
            if value:
                attr_parts.append(f'{key}="{rss_parser._escape_xml(str(value))}"')
        lines.append(f'    <podcast:remoteItem {" ".join(attr_parts)} />')
    lines.append('  </podcast:podroll>')


def _channel_artwork_url(slug: str, base: str, feed_auth_key: str | None,
                         storage_) -> str | None:
    """The cover-minuspod URL, or None when no cover is cached.

    cover-minuspod is the only public podcast-level artwork route (routes.py has no unbadged one), so
    a local feed with a cover always points here, unlike modify_feed which falls back to the raw upstream
    URL when watermarking is off. A local feed has no upstream URL to fall back to, and the auth-key
    self-heal fingerprint (routes.py:328-355) needs a self-consistent URL for an artwork-only feed.
    Mirrors the token scheme at rss_parser.py:966-980.
    """
    if not storage_.has_artwork(slug):
        return None
    version = storage_.artwork_version(slug)
    token = '-'.join(p for p in (version, feed_auth_key) if p)
    base_cover = f"{base}/{slug}/cover-minuspod"
    return f"{base_cover}-{token}.jpg" if token else f"{base_cover}.jpg"


def _safe_int_or_escaped(value) -> str:
    """int(value) when possible, else the XML-escaped string form.

    season_number/episode_number are INTEGER columns but SQLite's dynamic typing lets a non-numeric value
    in, which written raw could break the XML (same class of bug as an unescaped pubDate)."""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return rss_parser._escape_xml(str(value))


def _enclosure_audio_metadata(slug: str, ep: dict, storage_) -> tuple[str, float | None]:
    """Use the published file's length and duration, or the original fallback."""
    version = published_episode_version(ep)
    try:
        if version is not None:
            path = storage_.get_episode_path(slug, ep['episode_id'], version=version)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                pass
            except OSError:
                return '', ep.get('new_duration')
            else:
                return f' length="{size}"', ep.get('new_duration')
        path = storage_.get_original_path(slug, ep['episode_id'])
        size = path.stat().st_size
    except OSError:
        return '', ep.get('original_duration')
    return f' length="{size}"', ep.get('original_duration')


def _channel_open(title: str, channel_link: str, description: str, language: str = 'en',
                  itunes_block: bool = False) -> list[str]:
    """XML prologue through <generator>, shared by the local and recents renderers."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
            f'<!-- minuspod-rss-render-version:{RSS_RENDER_VERSION} -->',
            '<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
            'xmlns:content="http://purl.org/rss/1.0/modules/content/" '
            f'xmlns:podcast="{_PODCAST_NS}">',
            '<channel>',
            f'<title>{rss_parser._escape_xml(title)}</title>',
            f'<link>{rss_parser._escape_xml(channel_link)}</link>',
            f'<description><![CDATA[{rss_parser._escape_cdata(description)}]]></description>',
            f'<language>{rss_parser._escape_xml(language)}</language>',
            f'<lastBuildDate>{rss_parser._format_rfc2822(utc_now_iso())}</lastBuildDate>',
            '<generator>MinusPod</generator>']
    if itunes_block:
        lines.append(ITUNES_BLOCK_LINE)
    return lines


def _append_channel_image(lines: list, artwork_url: str | None, title: str, channel_link: str) -> None:
    if not artwork_url:
        return
    lines += ['<image>',
              f'  <url>{rss_parser._escape_xml(artwork_url)}</url>',
              f'  <title>{rss_parser._escape_xml(title)}</title>',
              f'  <link>{rss_parser._escape_xml(channel_link)}</link>',
              '</image>',
              f'<itunes:image href="{rss_parser._escape_xml(artwork_url)}" />']


def _append_local_episode_item(lines: list, slug: str, ep: dict, base: str,
                               storage_, feed_auth_key: str | None,
                               chapter_notes: dict[str, str],
                               title_prefix: str = '',
                               has_transcript: bool | None = None,
                               has_chapters: bool | None = None,
                               review_links: bool = False,
                               applied_cuts: dict[str, str] | None = None,
                               replacement_duration: float = 0.0) -> None:
    ep_id = ep['episode_id']
    item_json = _load_json_dict(ep.get('p20_item_json'))
    cuts = (applied_cuts or {}).get(ep_id)
    notes = chapter_notes.get(ep_id)
    description = item_body_html(
        ep.get('description'), slug=slug, episode_id=ep_id, cuts=cuts, notes=notes,
        replacement_duration=replacement_duration, review_links=review_links)

    lines.append('<item>')
    lines.append(f'  <title>{rss_parser._escape_xml(title_prefix + (ep.get("title") or ""))}</title>')
    lines.append(f'  <description><![CDATA[{rss_parser._escape_cdata(description)}]]></description>')
    content_line = content_encoded_line(
        ep.get('content_encoded'), slug=slug, episode_id=ep_id, cuts=cuts, notes=notes,
        replacement_duration=replacement_duration, review_links=review_links)
    if content_line:
        lines.append(content_line)
    lines.append(f'  <guid isPermaLink="false">{ep_id}</guid>')

    published_at = ep.get('published_at') or ep.get('created_at')
    if published_at:
        # _format_rfc2822 returns its input verbatim on a parse failure, so a malformed published_at
        # must still be escaped here or it can land in the tag body unescaped and break the document.
        pub_date = rss_parser._escape_xml(rss_parser._format_rfc2822(published_at))
        lines.append(f'  <pubDate>{pub_date}</pubDate>')

    # processed_version 0/None -> unversioned enclosure, so first play hits
    # the JIT processing path (episode_paths.episode_version_suffix).
    version = ep.get('processed_version') or None
    enclosure_url = episode_public_url(base, slug, ep_id, version=version,
                                       key=feed_auth_key)
    length_attr, duration = _enclosure_audio_metadata(slug, ep, storage_)
    duration = duration or ep.get('rss_duration')
    lines.append(f'  <enclosure url="{enclosure_url}" type="audio/mpeg"{length_attr} />')

    if duration:
        lines.append(f'  <itunes:duration>{int(duration)}</itunes:duration>')

    season = ep.get('season_number')
    if season:
        lines.append(f'  <itunes:season>{_safe_int_or_escaped(season)}</itunes:season>')

    episode_number = ep.get('episode_number')
    if episode_number:
        lines.append(f'  <itunes:episode>{_safe_int_or_escaped(episode_number)}</itunes:episode>')

    # Existence-only check (no read, no LRU touch): reading the cached image
    # would bump its mtime on every render and corrupt LRU eviction order.
    if storage_.has_episode_artwork(slug, ep_id):
        key_suffix = f"?key={feed_auth_key}" if feed_auth_key else ""
        artwork_url = f"{base}/episodes/{slug}/{ep_id}/artwork{key_suffix}"
        lines.append(f'  <itunes:image href="{rss_parser._escape_xml(artwork_url)}" />')

    # Same emitter as modify_feed; None flags fall back to per-item storage lookups.
    rss_parser._append_podcasting2_tags(
        lines, slug, ep_id, storage_, feed_auth_key,
        has_transcript=has_transcript, has_chapters=has_chapters)

    _emit_pc2_items(lines, 'person', item_json.get('person'), _PERSON_ATTRS)
    _emit_pc2_items(lines, 'location', item_json.get('location'), _LOCATION_ATTRS)

    lines.append('</item>')


def build_local_feed_xml(podcast: dict, episodes: list[dict], *, storage, db,
                         channel_link: str | None = None) -> str:
    """Render a local feed's RSS from DB rows only. No upstream fetch."""
    slug = podcast['slug']
    base = rss_parser._resolved_base_url()
    chapters, applied_cuts, podcasting2_flags = db.get_feed_render_details(podcast['id'])
    chapter_notes = chapter_notes_for(db, podcast, chapters)
    review_links = resolve_review_links_in_notes(db, podcast)
    feed_auth_key = active_feed_key(db)
    channel_json = _load_json_dict(podcast.get('p20_channel_json'))

    title = effective_channel_text(podcast.get('title_override'), podcast.get('title')) or slug
    channel_link = channel_link or f"{base}/{slug}"
    description = effective_channel_text(
        podcast.get('description_override'), podcast.get('description'), sanitize=True) or ''
    replacement_duration = get_replacement_duration()
    # 'auto' is a Whisper transcription-language pin, not an RSS language
    # code -- it must never leak into <language>.
    language = podcast.get('language_override') or 'en'
    if str(language).strip().lower() == 'auto':
        language = 'en'

    lines = _channel_open(title, channel_link, description, language,
                          itunes_block=feed_itunes_block_enabled(db))

    author = podcast.get('author')
    if author:
        lines.append(f'<itunes:author>{rss_parser._escape_xml(author)}</itunes:author>')

    explicit = podcast.get('explicit')
    if explicit is not None:
        lines.append(f'<itunes:explicit>{"true" if explicit else "false"}</itunes:explicit>')

    for category in load_json_list(podcast.get('categories')):
        if category:
            lines.append(f'<itunes:category text="{rss_parser._escape_xml(str(category))}" />')

    _append_channel_image(lines, _channel_artwork_url(slug, base, feed_auth_key, storage), title, channel_link)

    # Deliberately keyless (same as modify_feed, rss_parser.py:1413-1417):
    # the guid seed is the feed's stable identity, so folding the auth key
    # in would re-identify every feed on enable/rotate.
    guid = channel_json.get('guid') or compute_feed_guid(f"{base.rstrip('/')}/{slug}")
    if guid:
        lines.append(f'<podcast:guid>{rss_parser._escape_xml(guid)}</podcast:guid>')

    locked = str(channel_json.get('locked') or '').strip().lower()
    locked = locked if locked in ('yes', 'no') else 'yes'
    # owner is an optional contact email on the lock, independent of
    # yes/no (design spec section 6: "locked (default yes, owner email
    # optional)") -- emitted whenever set, regardless of the locked value.
    locked_owner = channel_json.get('locked_owner')
    owner_attr = (f' owner="{rss_parser._escape_xml(str(locked_owner))}"'
                 if locked_owner else '')
    lines.append(f'<podcast:locked{owner_attr}>{locked}</podcast:locked>')

    medium = channel_json.get('medium') or 'podcast'
    lines.append(f'<podcast:medium>{rss_parser._escape_xml(str(medium))}</podcast:medium>')

    _emit_pc2_items(lines, 'funding', channel_json.get('funding'), _FUNDING_ATTRS)
    _emit_pc2_items(lines, 'person', channel_json.get('person'), _PERSON_ATTRS)
    _emit_pc2_items(lines, 'license', channel_json.get('license'), _LICENSE_ATTRS)
    _emit_pc2_items(lines, 'location', channel_json.get('location'), _LOCATION_ATTRS)
    _emit_pc2_items(lines, 'txt', channel_json.get('txt'), _TXT_ATTRS)
    _emit_podroll(lines, channel_json.get('podroll'))

    lines.append('<podcast:txt purpose="ai-content">true</podcast:txt>')

    for ep in episodes:
        has_transcript, has_chapters = podcasting2_flags.get(ep['episode_id'], (False, False))
        _append_local_episode_item(lines, slug, ep, base, storage, feed_auth_key,
                                   chapter_notes=chapter_notes, review_links=review_links,
                                   has_transcript=has_transcript, has_chapters=has_chapters,
                                   applied_cuts=applied_cuts,
                                   replacement_duration=replacement_duration)

    lines.append('</channel>')
    lines.append('</rss>')
    return '\n'.join(lines)


def _fetch_local_feed_episodes(db_, podcast_id: int, limit: int | None,
                               processed_only: bool = False) -> list[dict]:
    """Episodes of a local feed, newest first, regardless of status.

    Unprocessed episodes still need an (unversioned) enclosure so the first play triggers JIT.
    get_episodes() enforces an offset and a status filter that do not fit, hence the direct query.
    ``limit`` None or 0 means every episode (the local-feed default: no upstream to reconcile against);
    a positive int keeps the newest N. ``processed_only`` keeps only episodes with a published file
    (YouTube feeds), including ones being reprocessed.
    """
    conn = db_.get_connection()
    served = f"AND {NOT_AWAITING_APPROVAL} "
    if processed_only:
        served += "AND processed_file IS NOT NULL "
    query = (f"SELECT * FROM episodes WHERE podcast_id = ? {served}"  # noqa: S608
             "ORDER BY COALESCE(published_at, created_at) DESC")
    params = [podcast_id]
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    cursor = conn.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]


def rebuild_local_feed(slug: str, podcast: dict | None = None) -> bool:
    """Render and persist a local feed's served RSS from DB rows only.

    Same postlude as feeds._build_and_save_served_rss (feeds.py:497-538): save the XML, stamp
    last_checked_at, invalidate the episode lookup cache. Returns False and logs a warning on any
    exception rather than propagating to the refresh scheduler.

    max_episodes is read straight off the row, not through db.get_max_episodes_for_podcast, whose NULL
    fallback to the global maxFeedEpisodes (default 300) is the subscribed-feed default. A local feed has
    no upstream to reconcile against, so NULL/0 means "serve every episode" and a positive value is a
    per-feed trim the operator opted into, with no 500 ceiling (their own archive).
    """
    podcast = podcast or db.get_podcast_by_slug(slug)
    if not podcast:
        return False
    try:
        raw_cap = podcast.get('max_episodes')
        feed_cap = int(raw_cap) if raw_cap else None
        episodes = _fetch_local_feed_episodes(db, podcast['id'], feed_cap)
        xml = build_local_feed_xml(podcast, episodes, storage=storage, db=db)
        storage.save_rss(slug, xml)
        db.update_podcast(slug, last_checked_at=utc_now_iso())
        invalidate_episode_lookup_cache(slug)
        return True
    except Exception as e:
        logger.warning(f"[{slug}] local feed RSS rebuild failed: {e}")
        return False
