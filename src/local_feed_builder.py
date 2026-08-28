"""RSS builder for local (imported-archive) feeds.

A local feed (``podcasts.feed_type == 'local'``) has no upstream source --
``rss_parser.modify_feed`` cannot run because there is no origin XML to
rewrite. This module renders the served feed entirely from the
podcasts/episodes rows instead, reusing the same line-assembly idioms, URL
shapes, and namespace declarations as ``modify_feed`` (rss_parser.py:873)
so a local feed is indistinguishable in shape from a subscribed one.

Podcasting 2.0 channel/episode extras (funding, person, license, location,
txt) are read from ``podcasts.p20_channel_json`` / ``episodes.p20_item_json``
as ``{"tag": [{"text": ..., <attr>: ...}, ...]}`` -- a list per tag, even for
the spec-singular ``license``/``location`` tags, so one small serializer
covers all five without a special case. ``podroll`` is channel-level too,
stored the same way as ``{"podroll": [{"feedGuid": ..., <attr>: ...}, ...]}``,
but rendered as a nested container of remoteItem children instead (see
``_emit_podroll``) since it has no flat ``<podcast:podroll>text</...>`` form.
"""
import json
import logging

from main_app import db, rss_parser, storage
from main_app.feed_auth import active_feed_key
from main_app.shared_state import invalidate_episode_lookup_cache
from utils.episode_paths import episode_public_url
from utils.feed_guid import compute_feed_guid
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

# podcast:podroll is a channel-level container of self-closing remoteItem
# children rather than a flat list of <podcast:{tag}> elements, so it isn't
# covered by _emit_pc2_items -- see _emit_podroll below. Attr order matches
# the podcastindex namespace's own remoteItem example (feedGuid, feedUrl,
# itemGuid, medium).
_PODROLL_REMOTE_ITEM_ATTRS = ('feedGuid', 'feedUrl', 'itemGuid', 'medium')


def _load_json_dict(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _load_json_list(raw: str | None) -> list:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


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
    """Append a ``<podcast:podroll>`` container wrapping one self-closing
    ``<podcast:remoteItem>`` per dict in ``entries`` that has a feedGuid.
    Emits nothing when there are no valid entries -- unlike the five
    _emit_pc2_items tags, an empty podroll container is not meaningful to a
    podcast app and would just be noise in the served feed."""
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

    cover-minuspod is the ONLY public podcast-level artwork route (routes.py
    has no plain/unbadged one), so a local feed always points here when it
    has a cover -- unlike modify_feed, which falls back to the raw upstream
    URL when watermarking is off. There is no upstream URL for a local feed
    to fall back to, and without a self-consistent URL here the auth-key
    self-heal fingerprint (routes.py:328-355) would have nothing to check
    for an artwork-only feed. Mirrors the token scheme at rss_parser.py:966-980.
    """
    if not storage_.has_artwork(slug):
        return None
    version = storage_.artwork_version(slug)
    token = '-'.join(p for p in (version, feed_auth_key) if p)
    base_cover = f"{base}/{slug}/cover-minuspod"
    return f"{base_cover}-{token}.jpg" if token else f"{base_cover}.jpg"


def _safe_int_or_escaped(value) -> str:
    """int(value) when possible, else the XML-escaped string form.

    season_number/episode_number are INTEGER columns, but SQLite's dynamic
    typing lets a stray non-numeric value slip in; without this, such a
    value would be written into the tag body raw and could break the XML
    (the same class of bug as an unescaped pubDate)."""
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return rss_parser._escape_xml(str(value))


def _enclosure_length_attr(slug: str, ep: dict, storage_, version) -> str:
    """`` length="N"`` (bytes, leading space included) for the enclosure
    tag, or '' when the size can't be determined.

    Processed episodes report the size of the served processed file;
    unprocessed ones report the retained original -- both already live on
    disk locally for a local feed, so declaring the byte count costs one
    stat() and some clients want it. Missing file / stat failure omits the
    attribute rather than erroring the whole feed render.
    """
    try:
        if ep.get('status') == 'processed':
            path = storage_.get_episode_path(slug, ep['episode_id'], version=version)
        else:
            path = storage_.get_original_path(slug, ep['episode_id'])
        size = path.stat().st_size
    except OSError:
        return ''
    return f' length="{size}"'


def _append_local_episode_item(lines: list, slug: str, ep: dict, base: str,
                               storage_, feed_auth_key: str | None) -> None:
    ep_id = ep['episode_id']
    item_json = _load_json_dict(ep.get('p20_item_json'))

    lines.append('<item>')
    lines.append(f'  <title>{rss_parser._escape_xml(ep.get("title") or "")}</title>')
    lines.append(f'  <description><![CDATA[{rss_parser._escape_cdata(ep.get("description") or "")}]]></description>')
    lines.append(f'  <guid isPermaLink="false">{ep_id}</guid>')

    published_at = ep.get('published_at') or ep.get('created_at')
    if published_at:
        # _format_rfc2822 returns its input verbatim on a parse failure, so
        # an operator-entered/malformed published_at must still be escaped
        # here -- otherwise it can land in the tag body unescaped and make
        # the whole document not-well-formed.
        pub_date = rss_parser._escape_xml(rss_parser._format_rfc2822(published_at))
        lines.append(f'  <pubDate>{pub_date}</pubDate>')

    # processed_version 0/None -> unversioned enclosure, so first play hits
    # the JIT processing path (episode_paths.episode_version_suffix).
    version = ep.get('processed_version') or None
    enclosure_url = episode_public_url(base, slug, ep_id, version=version,
                                       key=feed_auth_key)
    length_attr = _enclosure_length_attr(slug, ep, storage_, version)
    lines.append(f'  <enclosure url="{enclosure_url}" type="audio/mpeg"{length_attr} />')

    duration = ep.get('new_duration') or ep.get('original_duration')
    if duration:
        lines.append(f'  <itunes:duration>{int(duration)}</itunes:duration>')

    season = ep.get('season_number')
    if season:
        lines.append(f'  <itunes:season>{_safe_int_or_escaped(season)}</itunes:season>')

    episode_number = ep.get('episode_number')
    if episode_number:
        lines.append(f'  <itunes:episode>{_safe_int_or_escaped(episode_number)}</itunes:episode>')

    # Existence-only check (no read, no LRU touch) -- get_episode_artwork
    # would read the full cached image off disk and bump its mtime just to
    # answer a boolean, corrupting the artwork cache's LRU eviction order
    # on every render.
    if storage_.has_episode_artwork(slug, ep_id):
        key_suffix = f"?key={feed_auth_key}" if feed_auth_key else ""
        artwork_url = f"{base}/episodes/{slug}/{ep_id}/artwork{key_suffix}"
        lines.append(f'  <itunes:image href="{rss_parser._escape_xml(artwork_url)}" />')

    # Same emitter modify_feed uses (rss_parser.py:1096-1109), keyed
    # identically -- including key_suffix on both tags.
    rss_parser._append_podcasting2_tags(lines, slug, ep_id, storage_, feed_auth_key)

    _emit_pc2_items(lines, 'person', item_json.get('person'), _PERSON_ATTRS)
    _emit_pc2_items(lines, 'location', item_json.get('location'), _LOCATION_ATTRS)

    lines.append('</item>')


def build_local_feed_xml(podcast: dict, episodes: list[dict], *, storage, db) -> str:
    """Render a local feed's RSS from DB rows only. No upstream fetch."""
    slug = podcast['slug']
    base = rss_parser._resolved_base_url()
    feed_auth_key = active_feed_key(db)
    channel_json = _load_json_dict(podcast.get('p20_channel_json'))

    title = podcast.get('title') or slug
    channel_link = f"{base}/{slug}"
    description = podcast.get('description') or ''
    # 'auto' is a Whisper transcription-language pin, not an RSS language
    # code -- it must never leak into <language>.
    language = podcast.get('language_override') or 'en'
    if str(language).strip().lower() == 'auto':
        language = 'en'

    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append('<rss version="2.0" '
                 'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
                 f'xmlns:podcast="{_PODCAST_NS}">')
    lines.append('<channel>')
    lines.append(f'<title>{rss_parser._escape_xml(title)}</title>')
    lines.append(f'<link>{rss_parser._escape_xml(channel_link)}</link>')
    lines.append(f'<description><![CDATA[{rss_parser._escape_cdata(description)}]]></description>')
    lines.append(f'<language>{rss_parser._escape_xml(language)}</language>')
    lines.append(f'<lastBuildDate>{rss_parser._format_rfc2822(utc_now_iso())}</lastBuildDate>')
    lines.append('<generator>MinusPod</generator>')

    author = podcast.get('author')
    if author:
        lines.append(f'<itunes:author>{rss_parser._escape_xml(author)}</itunes:author>')

    explicit = podcast.get('explicit')
    if explicit is not None:
        lines.append(f'<itunes:explicit>{"true" if explicit else "false"}</itunes:explicit>')

    for category in _load_json_list(podcast.get('categories')):
        if category:
            lines.append(f'<itunes:category text="{rss_parser._escape_xml(str(category))}" />')

    artwork_url = _channel_artwork_url(slug, base, feed_auth_key, storage)
    if artwork_url:
        lines.append('<image>')
        lines.append(f'  <url>{rss_parser._escape_xml(artwork_url)}</url>')
        lines.append(f'  <title>{rss_parser._escape_xml(title)}</title>')
        lines.append(f'  <link>{rss_parser._escape_xml(channel_link)}</link>')
        lines.append('</image>')
        lines.append(f'<itunes:image href="{rss_parser._escape_xml(artwork_url)}" />')

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
        _append_local_episode_item(lines, slug, ep, base, storage, feed_auth_key)

    lines.append('</channel>')
    lines.append('</rss>')
    return '\n'.join(lines)


def _fetch_local_feed_episodes(db_, podcast_id: int, limit: int) -> list[dict]:
    """Up to `limit` episodes of a local feed, newest first, regardless of
    status -- unprocessed episodes still need an (unversioned) enclosure so
    the first play triggers JIT. get_episodes() enforces an offset and a
    status filter neither of which fits here, hence the direct query."""
    conn = db_.get_connection()
    cursor = conn.execute(
        """SELECT * FROM episodes WHERE podcast_id = ?
           ORDER BY COALESCE(published_at, created_at) DESC
           LIMIT ?""",
        (podcast_id, limit)
    )
    return [dict(row) for row in cursor.fetchall()]


def rebuild_local_feed(slug: str, podcast: dict | None = None) -> bool:
    """Render and persist a local feed's served RSS from DB rows only.

    Same postlude as feeds._build_and_save_served_rss (feeds.py:497-538):
    save the XML, stamp last_checked_at, invalidate the episode lookup
    cache. Returns False and logs a warning on any exception rather than
    letting it propagate to the refresh scheduler.
    """
    podcast = podcast or db.get_podcast_by_slug(slug)
    if not podcast:
        return False
    try:
        # Same clamp modify_feed's caller applies (feeds.py
        # _build_and_save_served_rss -> modify_feed's own max(1, min(..,
        # 500))) so a local feed's served item count is bounded the same
        # way a subscribed feed's is.
        feed_cap = max(1, min(db.get_max_episodes_for_podcast(slug, podcast=podcast), 500))
        episodes = _fetch_local_feed_episodes(db, podcast['id'], feed_cap)
        xml = build_local_feed_xml(podcast, episodes, storage=storage, db=db)
        storage.save_rss(slug, xml)
        db.update_podcast(slug, last_checked_at=utc_now_iso())
        invalidate_episode_lookup_cache(slug)
        return True
    except Exception as e:
        logger.warning(f"[{slug}] local feed RSS rebuild failed: {e}")
        return False
