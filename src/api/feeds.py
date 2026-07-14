"""Feed routes: /feeds/* endpoints."""
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import urlparse

import defusedxml.ElementTree as DefusedET

from flask import request, Response

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, get_feed_auth_key,
    _serialize_auto_process, _deserialize_auto_process,
    _serialize_nullable_bool, _deserialize_nullable_bool,
    _normalize_nullable_finite_float,
)
from config import FEED_REFRESH_FAILURE_ALERT_THRESHOLD
from differential_fetcher import is_likely_dai_feed
from positional_prior import compute_ad_distribution
# Module import (not `from rss_parser import RSSParser`) so tests patching
# rss_parser.RSSParser take effect at call time.
import rss_parser
from utils.language import LANGUAGE_CODE_RE
from utils.opml import build_opml_xml, modified_feed_url
from database.podcasts import EPISODE_STATUSES
from utils.url import validate_url, SSRFError
from utils.validation import is_valid_slug

from slugify import slugify as make_slug


def _normalize_language_override(value):
    """Validate the per-feed language override.

    Returns (db_value, error). db_value is the string to persist (None to
    clear the override) and error is a user-facing message or None.
    Accepts None / empty string to clear, 'auto' to pin auto-detect for
    this feed, or an ISO-639-1-ish code that matches the same regex as
    the global whisperLanguage setting.
    """
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "languageOverride must be a string, 'auto', or null"
    val = value.strip().lower()
    if not val:
        return None, None
    if val != 'auto' and not LANGUAGE_CODE_RE.match(val):
        return None, "languageOverride must be 'auto' or a 2-3 letter language code (e.g. 'en', 'de', 'pt')"
    return val, None


_TITLE_OVERRIDE_MAX = 500
# C0 control characters that XML 1.0 forbids even when escaped (everything
# below 0x20 except tab/LF/CR), plus DEL. Left in a title they make the served
# feed not-well-formed and every subscriber's app rejects it.
_XML_FORBIDDEN_CONTROLS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def _normalize_title_override(value):
    """Validate the per-feed display title override (#375).

    Returns (db_value, error). None / empty / whitespace clears the override
    (the served feed falls back to the source title). Strips surrounding
    whitespace, removes XML-forbidden control characters, and collapses any
    interior whitespace (newlines/tabs) so the served single-line <title>
    stays well-formed. Rejects non-strings and titles over 500 chars.
    """
    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, "titleOverride must be a string or null"
    val = _XML_FORBIDDEN_CONTROLS.sub('', value)
    val = re.sub(r'\s+', ' ', val).strip()
    if not val:
        return None, None
    if len(val) > _TITLE_OVERRIDE_MAX:
        return None, f"titleOverride must be {_TITLE_OVERRIDE_MAX} characters or fewer"
    return val, None


def _fetch_feed_content(url, timeout=30):
    """Fetch a feed body with one retry, shared by add_feed and the sourceUrl
    PATCH. Some hosts (e.g. Buzzsprout) 403 the first fetch from a new client
    but serve the retry; the circuit breaker inside fetch_feed still gates
    genuinely broken feeds. Returns (parser, content-or-None).
    """
    parser = rss_parser.RSSParser()
    content = None
    for attempt in (1, 2):
        content = parser.fetch_feed(url, timeout=timeout)
        if content:
            break
        if attempt == 1:
            time.sleep(0.5)
    return parser, content


def _validate_source_url(value):
    """Validate a replacement source feed URL (#484).

    Returns (url, error). Fetches and parses the URL before accepting it so a
    typo cannot silently break the feed. The column is NOT NULL, so there is
    no clear semantic: null/empty is rejected.
    """
    if not isinstance(value, str):
        return None, 'sourceUrl must be a non-empty string'
    url = value.strip()
    if not url:
        return None, 'sourceUrl must be a non-empty string'
    try:
        validate_url(url)
    except SSRFError as e:
        logger.warning(f"SSRF blocked in update_feed: {e} (url={url})")
        return None, f'Invalid feed URL: {e}'
    # 15s per attempt so a slow host cannot hang the PATCH toward the worker
    # timeout; the shared retry keeps 403-on-first-fetch hosts working here
    # the same way they do in add_feed.
    parser, content = _fetch_feed_content(url, timeout=15)
    if not content:
        return None, 'Could not fetch a valid RSS feed from this URL'
    parsed = parser.parse_feed(content)
    # parse_feed returns a feedparser object even for bozo input; requiring a
    # channel title or entries is what rejects HTML pages while still
    # accepting a legitimate zero-episode feed.
    if not (parsed and parsed.feed and (parsed.feed.get('title') or parsed.entries)):
        return None, 'URL did not return a parseable RSS feed'
    return url, None


def _normalize_detection_mode(value):
    """Validate the per-feed ad-detection mode (#350 keep-content).

    Returns (db_value, error). None / empty / 'blacklist' all clear the override
    to the default ad-removal behavior (stored NULL). 'keep_content' is stored.
    Any other value is rejected.
    """
    from config import DETECTION_MODES, DETECTION_MODE_BLACKLIST
    if value in (None, '', DETECTION_MODE_BLACKLIST):
        return None, None
    if value in DETECTION_MODES:
        return value, None
    return None, f"detectionMode must be one of: {', '.join(DETECTION_MODES)}"


from config import AUDIO_CUE_SCORE_MAX, AUDIO_CUE_SCORE_MIN

_CUE_SCORE_MIN = AUDIO_CUE_SCORE_MIN
_CUE_SCORE_MAX = AUDIO_CUE_SCORE_MAX

# (json_key, db_col, lo, hi) for the nullable-float per-feed override fields
# (cue knobs plus the Phase C held-for-review max-ad-duration cap; None = no cap).
_CUE_FLOAT_OVERRIDE_FIELDS = [
    ('cueTemplateScoreOverride',       'cue_template_score_override',          _CUE_SCORE_MIN, _CUE_SCORE_MAX),
    ('cuePairMinBreakOverride',        'cue_pair_min_break_override',          1.0, 600.0),
    ('cuePairMaxBreakOverride',        'cue_pair_max_break_override',          1.0, 3600.0),
    ('cuePairMaxBreakFractionOverride','cue_pair_max_break_fraction_override', 0.0, 1.0),
    ('cueSnapConfidenceOverride',      'cue_snap_confidence_override',         0.0, 1.0),
    ('cueSnapLeadOverride',            'cue_snap_lead_override',               0.5, 30.0),
    ('cueSnapLagOverride',             'cue_snap_lag_override',                0.5, 30.0),
    ('maxAdDurationOverride',          'max_ad_duration_override',             1.0, 3600.0),
]


def _normalize_cue_bool_override(value, field_name):
    """Validate a tri-state boolean override (null/false/true).

    Returns (db_value, error). None clears the override. True/False are stored
    as INTEGER 1/0. Non-boolean non-null values are rejected.
    """
    if not isinstance(value, bool) and value is not None:
        return None, f"{field_name} must be true, false, or null"
    return _serialize_nullable_bool(value), None


def _normalize_cue_float_override(value, field_name, lo, hi):
    """Validate a nullable float override within [lo, hi].

    Delegates to the shared validator which also rejects booleans, NaN and inf.
    """
    return _normalize_nullable_finite_float(value, field_name, lo, hi)


# (json_key, db_col) for the boundary-snap, held-review, and differential
# opt-in flags. Nullable-bool columns; NULL/0 read as off downstream.
_SNAP_FLAG_FIELDS = [
    ('silenceSnapEnabled',       'silence_snap_enabled'),
    ('transitionSnapEnabled',    'transition_snap_enabled'),
    ('cueGatedApproval',         'cue_gated_approval'),
    ('differentialFetchEnabled', 'differential_fetch_enabled'),
]

def _cue_override_fields(podcast) -> dict:
    """Cue override + boundary-snap flag + held-review slice of a feed response."""
    return {
        json_key: podcast.get(db_col)
        for json_key, db_col, _, _ in _CUE_FLOAT_OVERRIDE_FIELDS
        if json_key != 'cueTemplateScoreOverride'
    } | {
        'cueTemplateScoreOverride': podcast.get('cue_template_score_override'),
        'cueCreateFromPairsOverride': _deserialize_nullable_bool(
            podcast.get('cue_create_from_pairs_override')),
    } | {
        json_key: _deserialize_nullable_bool(podcast.get(db_col))
        for json_key, db_col in _SNAP_FLAG_FIELDS
    }


def _status_counts(podcast) -> dict:
    """Per-status episode counts of a feed response (#466).

    Keys deliberately match the frontend EPISODE_STATUS_COLORS keys: the DB
    status 'processed' is exposed under its API alias 'completed' (the same
    mapping api/episodes.py applies to episode responses) and comes from the
    pre-existing processed_count aggregate. LEFT JOIN SUMs are NULL for a
    feed with no episodes, hence the `or 0`.
    """
    counts = {s: podcast.get(f'status_{s}') or 0 for s in EPISODE_STATUSES}
    counts['completed'] = podcast.get('processed_count') or 0
    return counts


def _refresh_error_fields(podcast) -> dict:
    """Refresh-failure fields for a feed response (#516).

    Exposed only once the consecutive-failure count reaches the alert
    threshold, so the UI's "Refresh failing" marker matches the webhook/
    email alert semantics instead of flagging a single transient blip.
    """
    failing = (podcast.get('refresh_failure_count') or 0) \
        >= FEED_REFRESH_FAILURE_ALERT_THRESHOLD
    return {
        'lastRefreshError': podcast.get('last_refresh_error') if failing else None,
        'lastRefreshErrorAt': podcast.get('last_refresh_error_at') if failing else None,
    }


def _slug_from_url_path(source_url: str) -> Optional[str]:
    # Final-resort slug derivation when neither an upstream OPML title nor
    # an RSS <title> is available. Strips ``.xml`` / ``.rss`` suffixes
    # because they would otherwise become part of the slug. Returns None
    # if the URL has no usable path or hostname.
    parsed = urlparse(source_url)
    slug_base = parsed.path.strip('/').split('/')[-1] or parsed.netloc
    slug_base = slug_base.replace('.xml', '').replace('.rss', '')
    candidate = make_slug(slug_base) if slug_base else None
    # A purely-numeric path segment (e.g. a feed id like /1411126.rss) makes a
    # meaningless slug; return None so the caller asks the user for one rather
    # than committing a numeric slug (feeds-api-1).
    if candidate and candidate.isdigit():
        return None
    return candidate

logger = logging.getLogger('podcast.api')


# ========== Feed Endpoints ==========

def _public_feed_url(slug, key):
    """Subscribable feed URL, carrying ?key= while feed auth is enabled."""
    return modified_feed_url(os.environ.get('BASE_URL', 'http://localhost:8000'),
                             slug, key)


@api.route('/feeds', methods=['GET'])
@log_request
def list_feeds():
    """List all podcast feeds with metadata."""
    db = get_database()

    podcasts = db.get_all_podcasts()
    feed_auth_key = get_feed_auth_key(db)

    feeds = []
    for podcast in podcasts:
        feed_url = _public_feed_url(podcast['slug'], feed_auth_key)

        feeds.append({
            'slug': podcast['slug'],
            'title': podcast['title'] or podcast['slug'],
            'titleOverride': podcast.get('title_override'),
            'detectionMode': podcast.get('detection_mode'),
            **_cue_override_fields(podcast),
            'sourceUrl': podcast['source_url'],
            'feedUrl': feed_url,
            'artworkUrl': f"/api/v1/feeds/{podcast['slug']}/artwork" if podcast.get('artwork_cached') else podcast.get('artwork_url'),
            'episodeCount': podcast.get('episode_count', 0),
            'processedCount': podcast.get('processed_count', 0),
            'statusCounts': _status_counts(podcast),
            'lastRefreshed': podcast.get('last_checked_at'),
            **_refresh_error_fields(podcast),
            'createdAt': podcast.get('created_at'),
            'lastEpisodeDate': podcast.get('last_episode_date'),
            'networkId': podcast.get('network_id'),
            'daiPlatform': podcast.get('dai_platform'),
            'maxEpisodes': podcast.get('max_episodes'),
            'onlyExposeProcessedEpisodes': _deserialize_nullable_bool(podcast.get('only_expose_processed_episodes')),
        })

    return json_response({
        'feeds': feeds,
        # Stamped whenever an all-feeds refresh pass finishes (15-minute
        # scheduler or the manual Refresh All action); null until the
        # first pass completes.
        'lastRefreshCompletedAt': db.get_setting('feeds_last_refresh_completed_at'),
    })


@api.route('/feeds', methods=['POST'])
@limiter.limit("3 per minute")
@log_request
def add_feed():
    """Add a new podcast feed.

    OPML bulk-import lives on its own endpoint with its own limiter, so the
    feeds POST limit is tuned for interactive use.
    """
    data = request.get_json()

    if not data or 'sourceUrl' not in data:
        logger.warning("Missing sourceUrl in POST /feeds request")
        return error_response('sourceUrl is required', 400)

    source_url = data['sourceUrl'].strip()
    if not source_url:
        return error_response('sourceUrl cannot be empty', 400)

    # SSRF protection: validate URL before any outbound request
    try:
        validate_url(source_url)
    except SSRFError as e:
        logger.warning(f"SSRF blocked in add_feed: {e} (url={source_url})")
        return error_response(f'Invalid feed URL: {e}', 400)

    # Generate slug from podcast name or use provided slug
    slug = data.get('slug', '').strip()
    if not slug:
        parser, feed_content = _fetch_feed_content(source_url)

        if feed_content:
            parsed_feed = parser.parse_feed(feed_content)
            if parsed_feed and parsed_feed.feed:
                title = parsed_feed.feed.get('title', '')
                if title:
                    slug = make_slug(title)

        # URL-path fallback. Without this, a feed whose host blocks the
        # initial fetch or whose <title> is empty would force the user to
        # invent a slug by hand. The OPML import path has had this
        # fallback for a while; the single-add endpoint was missing it.
        if not slug:
            slug = _slug_from_url_path(source_url)

    if not slug:
        return error_response(
            "Could not derive a slug from the feed URL. "
            "Provide a 'slug' in the request.",
            400,
        )

    # Validate the final slug. A client may supply one directly, bypassing the
    # derivation path; is_valid_slug rejects reserved words, uppercase, spaces,
    # path-traversal characters, and over-length values (feeds-api-1).
    if not is_valid_slug(slug):
        return error_response(
            f'Invalid slug "{slug}": use lowercase letters, digits and hyphens '
            '(max 200 chars), not a reserved word.',
            400,
        )

    db = get_database()

    # Check if slug already exists
    existing = db.get_podcast_by_slug(slug)
    if existing:
        return error_response(f'Feed with slug "{slug}" already exists', 409)

    # Validate optional fields before creating the row so a bad value cannot
    # leave an orphaned, unrefreshed feed that then blocks retry with 409.
    max_ep = data.get('maxEpisodes')
    if max_ep is not None:
        try:
            max_ep = max(10, min(int(max_ep), 500))
        except (ValueError, TypeError):
            return error_response('maxEpisodes must be an integer', 400)

    lang_val = None
    if 'languageOverride' in data:
        lang_val, lang_err = _normalize_language_override(data['languageOverride'])
        if lang_err:
            return error_response(lang_err, 400)

    # Create podcast
    try:
        db.create_podcast(slug, source_url)
        logger.info(f"Created new feed: {slug} -> {source_url}")

        # Apply auto-process override if provided (before initial refresh)
        auto_process_override = data.get('autoProcessOverride')
        db_value = _serialize_auto_process(auto_process_override)
        if db_value is not None:
            db.update_podcast(slug, auto_process_override=db_value)

        # Apply max_episodes if provided (validated above)
        if max_ep is not None:
            db.update_podcast(slug, max_episodes=max_ep)

        # Apply language override if provided (validated above)
        if lang_val is not None:
            db.update_podcast(slug, language_override=lang_val)

        if 'onlyExposeProcessedEpisodes' in data:
            db.update_podcast(
                slug,
                only_expose_processed_episodes=_serialize_nullable_bool(
                    data['onlyExposeProcessedEpisodes']),
            )

        # Invalidate feed cache since we added a new feed
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Trigger initial refresh in background
        try:
            from main_app.feeds import refresh_rss_feed
            refresh_rss_feed(slug, source_url)
        except Exception as e:
            logger.warning(f"Initial refresh failed for {slug}: {e}")

        return json_response({
            'slug': slug,
            'sourceUrl': source_url,
            'feedUrl': _public_feed_url(slug, get_feed_auth_key(db)),
            'message': 'Feed added successfully'
        }, 201)

    except Exception:
        logger.exception("Failed to add feed")
        return error_response('Failed to add feed', 500)


@api.route('/feeds/import-opml', methods=['POST'])
@limiter.limit("5 per minute")
@log_request
def import_opml():
    """Import podcast feeds from an OPML file.

    Accepts a multipart form upload with an 'opml' file field.
    Returns counts of successfully imported and failed feeds.
    """
    if 'opml' not in request.files:
        return error_response('No OPML file provided', 400)

    opml_file = request.files['opml']
    if not opml_file.filename:
        return error_response('Empty file name', 400)

    # Check file extension
    if not opml_file.filename.lower().endswith(('.opml', '.xml')):
        return error_response('File must be .opml or .xml', 400)

    try:
        content = opml_file.read().decode('utf-8')
        root = DefusedET.fromstring(content)
    except DefusedET.ParseError as e:
        logger.error(f"OPML parse error: {e}")
        return error_response('Invalid OPML file format', 400)
    except UnicodeDecodeError as e:
        logger.error(f"OPML encoding error: {e}")
        return error_response('File must be UTF-8 encoded', 400)

    # Find all outline elements with xmlUrl (RSS feeds). Cap the count so a huge
    # OPML can't tie up a worker doing thousands of synchronous DNS lookups and
    # feed creations in one request (feeds-api-4).
    MAX_OPML_FEEDS = 500
    feeds_found = []
    for outline in root.iter('outline'):
        xml_url = outline.get('xmlUrl')
        if xml_url:
            title = outline.get('text') or outline.get('title') or ''
            feeds_found.append({'url': xml_url, 'title': title})
            if len(feeds_found) >= MAX_OPML_FEEDS:
                logger.warning("OPML import truncated at %d feeds", MAX_OPML_FEEDS)
                break

    if not feeds_found:
        return error_response('No RSS feeds found in OPML file', 400)

    # Import feeds
    db = get_database()
    from rss_parser import RSSParser

    imported = []
    failed = []
    skipped = []

    for feed_info in feeds_found:
        source_url = feed_info['url'].strip()
        title = feed_info['title'].strip()

        # SSRF protection: validate each feed URL
        try:
            validate_url(source_url)
        except SSRFError as e:
            logger.warning(f"SSRF blocked in OPML import: {e} (url={source_url})")
            failed.append({'url': source_url, 'error': f'Invalid URL: {e}'})
            continue

        # Generate slug
        slug = make_slug(title) if title else None

        # If no title, try to fetch from RSS
        if not slug:
            rss_parser = RSSParser()
            try:
                feed_content = rss_parser.fetch_feed(source_url)
                if feed_content:
                    parsed_feed = rss_parser.parse_feed(feed_content)
                    if parsed_feed and parsed_feed.feed:
                        fetched_title = parsed_feed.feed.get('title', '')
                        if fetched_title:
                            slug = make_slug(fetched_title)
            except Exception:
                pass

        # Fallback to URL-based slug (shared with single-add endpoint).
        if not slug:
            slug = _slug_from_url_path(source_url)

        if not slug:
            failed.append({'url': source_url, 'error': 'Could not generate slug'})
            continue

        # Check if slug already exists
        existing = db.get_podcast_by_slug(slug)
        if existing:
            skipped.append({'url': source_url, 'slug': slug, 'reason': 'Already exists'})
            continue

        # Create podcast
        try:
            db.create_podcast(slug, source_url, title or None)
            imported.append({'url': source_url, 'slug': slug})
            logger.info(f"OPML import: Created feed {slug}")
        except Exception as e:
            failed.append({'url': source_url, 'error': 'import failed'})
            logger.error(f"OPML import failed for {source_url}: {e}")

    # Invalidate feed cache
    if imported:
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Trigger refresh for imported feeds
        try:
            from main_app.feeds import refresh_rss_feed
            for feed in imported[:5]:  # Limit to first 5 to avoid overload
                podcast = db.get_podcast_by_slug(feed['slug'])
                if podcast:
                    refresh_rss_feed(feed['slug'], podcast['source_url'])
        except Exception as e:
            logger.warning(f"OPML import: Failed to trigger refreshes: {e}")

    logger.info(
        f"OPML import complete: {len(imported)} imported, "
        f"{len(skipped)} skipped, {len(failed)} failed"
    )

    return json_response({
        'imported': len(imported),
        'skipped': len(skipped),
        'failed': len(failed),
        'feeds': {
            'imported': imported,
            'skipped': skipped,
            'failed': failed
        }
    }, 201 if imported else 200)


@api.route('/feeds/export-opml', methods=['GET'])
@log_request
def export_opml():
    """Export all podcast feeds as an OPML 2.0 download (admin UI)."""
    mode = request.args.get('mode', 'original')
    if mode not in ('original', 'modified'):
        return error_response('mode must be "original" or "modified"', 400)

    db = get_database()
    podcasts = db.get_all_podcasts()
    # Keyed while feed auth is enabled, so a re-import after enable or
    # rotation subscribes apps with working URLs.
    base_url = os.environ.get('BASE_URL', 'http://localhost:8000')
    xml_output = build_opml_xml(podcasts, mode, base_url, get_feed_auth_key(db))

    filename = 'minuspod-feeds.opml' if mode == 'original' else 'minuspod-feeds-modified.opml'
    logger.info(f"Exported {len(podcasts)} feeds as OPML (mode={mode})")

    return Response(
        xml_output,
        # octet-stream, not application/xml: iOS Safari/Files rewrites the
        # download extension to match a recognized MIME type (.xml) and drops
        # the .opml in Content-Disposition; octet-stream has no such mapping,
        # so the .opml filename survives.
        mimetype='application/octet-stream',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"'
        }
    )


@api.route('/feeds/<slug>', methods=['GET'])
@log_request
def get_feed(slug):
    """Get a single podcast feed by slug."""
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    feed_url = _public_feed_url(slug, get_feed_auth_key(db))

    # Convert auto_process_override from string to boolean/null
    auto_process_override_result = _deserialize_auto_process(podcast.get('auto_process_override'))

    # DAI-likelihood hint from recent enclosure URLs (Layer 3). Prefix
    # services embed the chain in the URL, so no network round trip needed.
    recent_episodes, _ = db.get_episodes(slug, limit=5)
    dai_likely = is_likely_dai_feed(
        [e.get('original_url') for e in recent_episodes])

    return json_response({
        'slug': podcast['slug'],
        'title': podcast['title'] or podcast['slug'],
        'description': podcast.get('description'),
        'sourceUrl': podcast['source_url'],
        'feedUrl': feed_url,
        'artworkUrl': f"/api/v1/feeds/{podcast['slug']}/artwork" if podcast.get('artwork_cached') else podcast.get('artwork_url'),
        'episodeCount': podcast.get('episode_count', 0),
        'processedCount': podcast.get('processed_count', 0),
        'statusCounts': _status_counts(podcast),
        'lastRefreshed': podcast.get('last_checked_at'),
        **_refresh_error_fields(podcast),
        'createdAt': podcast.get('created_at'),
        'networkId': podcast.get('network_id'),
        'daiPlatform': podcast.get('dai_platform'),
        'daiLikely': dai_likely,
        'networkIdOverride': podcast.get('network_id_override'),
        'autoProcessOverride': auto_process_override_result,
        'languageOverride': podcast.get('language_override'),
        'titleOverride': podcast.get('title_override'),
        'detectionMode': podcast.get('detection_mode'),
        **_cue_override_fields(podcast),
        'maxEpisodes': podcast.get('max_episodes'),
        'onlyExposeProcessedEpisodes': _deserialize_nullable_bool(podcast.get('only_expose_processed_episodes')),
    })


@api.route('/feeds/<slug>', methods=['PATCH'])
@log_request
def update_feed(slug):
    """Update podcast feed settings (network, DAI platform, etc.)."""
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    data = request.get_json()
    if not data:
        return error_response('No data provided', 400)

    # Map API field names to database field names. Note: the source `title` is
    # RSS-managed (a refresh overwrites it), so it is intentionally NOT editable
    # here -- user renames go through `titleOverride` below (#375).
    field_map = {
        'networkId': 'network_id',
        'daiPlatform': 'dai_platform',
        'networkIdOverride': 'network_id_override',
        'description': 'description'
    }

    updates = {}
    for api_field, db_field in field_map.items():
        if api_field in data:
            updates[db_field] = data[api_field]

    # Handle auto-process override specially (can be null, true, or false).
    # None passes through to DB as NULL (clears the override) -- unlike add_feed
    # which guards with `if db_value is not None` since there's nothing to clear yet.
    if 'autoProcessOverride' in data:
        updates['auto_process_override'] = _serialize_auto_process(data['autoProcessOverride'])

    if 'languageOverride' in data:
        lang_val, lang_err = _normalize_language_override(data['languageOverride'])
        if lang_err:
            return error_response(lang_err, 400)
        updates['language_override'] = lang_val

    if 'titleOverride' in data:
        title_val, title_err = _normalize_title_override(data['titleOverride'])
        if title_err:
            return error_response(title_err, 400)
        updates['title_override'] = title_val

    if 'detectionMode' in data:
        mode_val, mode_err = _normalize_detection_mode(data['detectionMode'])
        if mode_err:
            return error_response(mode_err, 400)
        updates['detection_mode'] = mode_val

    for json_key, db_col, lo, hi in _CUE_FLOAT_OVERRIDE_FIELDS:
        if json_key in data:
            v, err = _normalize_cue_float_override(data[json_key], json_key, lo, hi)
            if err:
                return error_response(err, 400)
            updates[db_col] = v

    if 'cueCreateFromPairsOverride' in data:
        v, err = _normalize_cue_bool_override(data['cueCreateFromPairsOverride'],
                                               'cueCreateFromPairsOverride')
        if err:
            return error_response(err, 400)
        updates['cue_create_from_pairs_override'] = v

    for json_key, db_col in _SNAP_FLAG_FIELDS:
        if json_key in data:
            v, err = _normalize_cue_bool_override(data[json_key], json_key)
            if err:
                return error_response(err, 400)
            updates[db_col] = v

    # Handle maxEpisodes
    if 'maxEpisodes' in data:
        max_ep = data['maxEpisodes']
        if max_ep is not None:
            try:
                max_ep = max(10, min(int(max_ep), 500))
            except (ValueError, TypeError):
                return error_response('maxEpisodes must be an integer', 400)
        updates['max_episodes'] = max_ep

    if 'onlyExposeProcessedEpisodes' in data:
        updates['only_expose_processed_episodes'] = _serialize_nullable_bool(
            data['onlyExposeProcessedEpisodes'])

    # Validated last so every cheap field validation can 400 before the
    # network fetch inside _validate_source_url runs. An unchanged URL is a
    # no-op: skipping it avoids the validation fetch, the validator clear,
    # and the forced refresh for retried or blind-PUT-style requests.
    if 'sourceUrl' in data and not (
            isinstance(data['sourceUrl'], str)
            and data['sourceUrl'].strip() == podcast['source_url']):
        new_url, url_err = _validate_source_url(data['sourceUrl'])
        if url_err:
            return error_response(url_err, 400)
        updates['source_url'] = new_url

    if not updates:
        return error_response('No valid fields to update', 400)

    try:
        db.update_podcast(slug, **updates)
        logger.info(f"Updated feed {slug}: {updates}")

        # Invalidate feed cache since we modified a feed
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Return updated feed data
        podcast = db.get_podcast_by_slug(slug)

        # Settings changes that alter the served RSS body must regenerate it.
        # Clearing etag/last_modified first ensures that if the force-refresh
        # below throws, the next scheduled refresh cannot 304 and will fully
        # regenerate the feed with the new settings applied. title_override
        # rewrites the served channel <title> (#375), so it belongs here too.
        # source_url repoints the feed entirely (#484): the stored validators
        # belong to the old host and could false-304 against the new one, and
        # the immediate refresh pulls from the new URL (podcast was re-read
        # above, so it carries the new value).
        if ('max_episodes' in updates or 'only_expose_processed_episodes' in updates
                or 'title_override' in updates or 'source_url' in updates):
            db.update_podcast_etag(slug, None, None)
            try:
                from main_app.feeds import refresh_rss_feed
                refresh_rss_feed(slug, podcast['source_url'], force=True)
            except Exception as e:
                logger.warning(f"Feed refresh after settings change failed for {slug}: {e}")

        return json_response({
            'slug': podcast['slug'],
            'title': podcast['title'] or podcast['slug'],
            'sourceUrl': podcast['source_url'],
            'networkId': podcast.get('network_id'),
            'daiPlatform': podcast.get('dai_platform'),
            'networkIdOverride': podcast.get('network_id_override'),
            'languageOverride': podcast.get('language_override'),
            'titleOverride': podcast.get('title_override'),
            'detectionMode': podcast.get('detection_mode'),
            **_cue_override_fields(podcast),
            'maxEpisodes': podcast.get('max_episodes'),
            'onlyExposeProcessedEpisodes': _deserialize_nullable_bool(podcast.get('only_expose_processed_episodes')),
            'statusCounts': _status_counts(podcast),
            'feedUrl': _public_feed_url(slug, get_feed_auth_key(db))
        })
    except Exception:
        logger.exception(f"Failed to update feed {slug}")
        return error_response('Failed to update feed', 500)


@api.route('/feeds/<slug>', methods=['DELETE'])
@log_request
def delete_feed(slug):
    """Delete a podcast feed and all associated data."""
    db = get_database()
    storage = get_storage()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    try:
        # Delete from database (cascade deletes episodes)
        db.delete_podcast(slug)

        # Invalidate feed cache since we deleted a feed
        from main_app.feeds import invalidate_feed_cache
        invalidate_feed_cache()

        # Delete files
        storage.cleanup_podcast_dir(slug)

        logger.info(f"Deleted feed: {slug}")
        return json_response({'message': 'Feed deleted', 'slug': slug})

    except Exception:
        logger.exception(f"Failed to delete feed {slug}")
        return error_response('Failed to delete feed', 500)


@api.route('/feeds/<slug>/refresh', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def refresh_feed(slug):
    """Refresh a single podcast feed.

    Optional request body:
    {
        "force": true  // Force full refresh, bypassing conditional GET (304)
    }
    """
    db = get_database()

    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)

    if not podcast.get('source_url'):
        return error_response('Feed has no source URL', 400)

    # Check for force parameter
    force = False
    data = request.get_json(silent=True)
    if data and data.get('force'):
        force = True
        # Clear ETag to force non-conditional fetch
        db.update_podcast_etag(slug, None, None)
        logger.info(f"Force refresh requested for {slug}, cleared ETag")

    try:
        from main_app.feeds import refresh_rss_feed
        refresh_rss_feed(slug, podcast['source_url'], force=force)

        # Get updated info
        podcast = db.get_podcast_by_slug(slug)
        episodes, total = db.get_episodes(slug)

        logger.info(f"Refreshed feed: {slug}")
        return json_response({
            'slug': slug,
            'message': 'Feed refreshed',
            'episodeCount': total,
            'lastRefreshed': podcast.get('last_checked_at')
        })

    except Exception:
        logger.exception(f"Failed to refresh feed {slug}")
        return error_response('Failed to refresh feed', 500)


@api.route('/feeds/refresh', methods=['POST'])
@limiter.limit("2 per minute")
@log_request
def refresh_all_feeds():
    """Refresh all podcast feeds.

    Optional request body:
    {
        "force": true  // Force full refresh for all feeds, bypassing conditional GET (304)
    }
    """
    try:
        db = get_database()

        data = request.get_json(silent=True)
        force = bool(data and data.get('force'))

        from main_app.feeds import refresh_all_feeds as do_refresh
        do_refresh(force=force)

        podcasts = db.get_all_podcasts()

        logger.info(f"Refreshed all feeds (force={force})")
        return json_response({
            'message': 'All feeds refreshed',
            'feedCount': len(podcasts)
        })

    except Exception:
        logger.exception("Failed to refresh all feeds")
        return error_response('Failed to refresh feeds', 500)


@api.route('/feeds/refresh-artwork', methods=['POST'])
@limiter.limit("2 per minute")
@log_request
def refresh_artwork():
    """Re-pull cover art for all feeds and rebuild their served RSS so the
    cover-art badge setting (issue #420) takes effect. Unlike a feed refresh,
    this does not re-discover or queue episodes, so it never triggers processing.
    """
    try:
        from main_app.feeds import refresh_all_artwork
        count = refresh_all_artwork()
        logger.info(f"Refreshed artwork for {count} feed(s)")
        return json_response({'message': 'Artwork refreshed', 'feedCount': count})
    except Exception:
        logger.exception("Failed to refresh artwork")
        return error_response('Failed to refresh artwork', 500)


@api.route('/feeds/regenerate', methods=['POST'])
@limiter.limit("2 per minute")
@log_request
def regenerate_feeds():
    """Rebuild every served RSS with the current URL settings (feed auth key,
    cover badge, BASE_URL). Re-fetches each upstream source feed but does not
    re-discover or queue episodes, so it never triggers processing and never
    touches episode rows or stats. Used after enabling feed auth or rotating
    the key so existing feeds embed it.
    """
    try:
        from main_app.feeds import rebuild_all_served_feeds
        count = rebuild_all_served_feeds()
        logger.info(f"Regenerated served RSS for {count} feed(s)")
        return json_response({'message': 'Feeds regenerated', 'feedCount': count})
    except Exception:
        logger.exception("Failed to regenerate feeds")
        return error_response('Failed to regenerate feeds', 500)


def _extract_artwork_url_from_feed(source_url: str) -> Optional[str]:
    """Extract artwork URL from a podcast's RSS feed."""
    try:
        from rss_parser import RSSParser
        rss_parser = RSSParser()
        feed_content = rss_parser.fetch_feed(source_url)
        if not feed_content:
            return None
        # Pass raw XML; see extract_podcast_artwork_url docstring on why
        # the feedparser path is unreliable for the channel image.
        return rss_parser.extract_podcast_artwork_url(feed_content)
    except Exception as e:
        logger.warning(f"Failed to extract artwork URL from feed: {e}")
    return None


@api.route('/feeds/<slug>/artwork', methods=['GET'])
@log_request
def get_artwork(slug):
    """Get cached artwork for a podcast."""
    storage = get_storage()

    artwork = storage.get_artwork(slug)
    if not artwork:
        # Only auto-recover when the DB flag claims artwork IS cached
        # (the file got deleted out from under us). When cached=0, a prior
        # download failed (size cap, content-type rejection, fetch error)
        # and retrying on every request just burns ~200ms per request and
        # hammers the upstream host. The 15-minute refresh cycle retries
        # downloads naturally; let it do the work.
        db = get_database()
        podcast = db.get_podcast_by_slug(slug)
        if podcast and podcast.get('artwork_cached'):
            db.update_podcast(slug, artwork_cached=0)
            artwork_url = podcast.get('artwork_url')
            if not artwork_url and podcast.get('source_url'):
                artwork_url = _extract_artwork_url_from_feed(podcast['source_url'])
                if artwork_url:
                    db.update_podcast(slug, artwork_url=artwork_url)
            if artwork_url:
                storage.download_artwork(slug, artwork_url)
                artwork = storage.get_artwork(slug)

    if not artwork:
        return error_response('Artwork not found', 404)

    image_data, content_type = artwork
    # content_type was validated by magic-number check on write; tell the
    # browser not to sniff it and deny any script loading from this
    # response even if a downstream intermediary rewrites the type.
    response = Response(image_data, mimetype=content_type)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Content-Security-Policy'] = "default-src 'none'"
    return response


# ========== Tag endpoints ==========

@api.route('/feeds/<slug>/tags', methods=['GET'])
@log_request
def get_feed_tags(slug):
    """Return the source breakdown of a podcast's tags.

    Output: {effective: [...], rss: [...], episode: [...], user: [...]}
    """
    db = get_database()
    if not db.get_podcast_by_slug(slug):
        return error_response('Feed not found', 404)
    return json_response(db.get_podcast_tags(slug))


@api.route('/feeds/<slug>/ad-distribution', methods=['GET'])
@log_request
def get_ad_distribution(slug):
    """Where this feed's ad cuts historically land across an episode.

    Setting-independent (does not require the positional-prior experiment to
    be enabled): purely informational for the feed detail panel.
    """
    db = get_database()
    if not db.get_podcast_by_slug(slug):
        return error_response('Feed not found', 404)

    dist = compute_ad_distribution(db, slug)
    return json_response({
        'slug': slug,
        'episodesConsidered': dist.episodes_considered,
        'medianDurationSeconds': dist.median_duration,
        'bucketCount': dist.bucket_count,
        'buckets': dist.buckets,
        'totalEvents': dist.total_events,
        'zones': [
            {
                'center': z.center,
                'low': z.low,
                'high': z.high,
                'support': z.support,
                'boost': z.boost,
            }
            for z in dist.zones
        ],
    })


@api.route('/feeds/<slug>/tags', methods=['PUT'])
@log_request
def update_feed_tags(slug):
    """Update a podcast's user-added tags. Body: {user_tags: ['tag1', ...]}.

    Validates each tag against VALID_TAGS. The denormalized `tags` field
    on the row is rewritten as the union of (existing rss + new user + episode tags).
    """
    from utils.community_tags import valid_tags
    db = get_database()
    if not db.get_podcast_by_slug(slug):
        return error_response('Feed not found', 404)
    data = request.get_json() or {}
    user_tags = data.get('user_tags')
    if not isinstance(user_tags, list):
        return error_response('user_tags must be a list of strings', 400)

    vt = valid_tags()
    bad = [t for t in user_tags if t not in vt]
    if bad:
        return error_response(f'unknown tags: {", ".join(bad)}', 400)

    db.set_podcast_tags(slug, user_tags=user_tags)
    return json_response(db.get_podcast_tags(slug))
