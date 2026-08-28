"""Local-feed episode upload, edit, bulk-edit, delete, and per-episode
artwork upload APIs (#625 Task 8).

Local feeds (``podcasts.feed_type == 'local'``) have no upstream RSS to
source episode content or edits from, so these routes exist only there --
a subscribed feed 400s. ``local_feed_builder.rebuild_local_feed`` and
``database.podcasts.is_local_feed`` are both imported lazily inside each
function body (not at module load) for the same reason ``api/feeds.py``
does it (see its ``_p20_tag_attrs`` docstring): ``local_feed_builder``
pulls in ``main_app`` at its own module level, and ``main_app/__init__.py``
imports the ``api`` package (which imports this module) before it finishes
initializing -- a module-level import here would be circular.
"""
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from flask import request

from api import (api, limiter, log_request, json_response, error_response,
                 get_database, get_storage)
from api.episodes import _episode_base_json
from api.feeds import _validate_p20_items, _p20_tag_attrs
from config import MIN_PRESERVED_CHAPTERS
from database.podcasts import is_local_feed
from database.queue import compute_queue_priority
from embedded_chapters import probe_chapters
from storage import _detect_image_mime
from utils.audio import get_audio_duration
from utils.constants import EpisodeStatus
from utils.time import ISO_FORMAT, utc_now_iso
from utils.validation import is_valid_episode_id

logger = logging.getLogger('podcast.api')

MAX_BULK_EPISODES = 500

# Episode-level (p20_item_json) tags. Channel-level p20 (podcasts.p20_channel_json,
# see api/feeds.py's _validate_p20) additionally supports funding/license/txt,
# but local_feed_builder only ever reads person/location off an episode row
# (build_local_feed_xml's per-item emitter, local_feed_builder.py:163-164).
_ITEM_P20_TAGS = ('person', 'location')


def _require_local_feed(db, slug):
    """Fetch the podcast row, 404 if missing, 400 if not a local feed.

    Returns (podcast, None) on success or (None, error_response) to return
    directly from the caller.
    """
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return None, error_response('Feed not found', 404)
    if not is_local_feed(podcast):
        return None, error_response(
            'This endpoint is only available for local feeds', 400)
    return podcast, None


def _rebuild(slug, podcast=None):
    from local_feed_builder import rebuild_local_feed
    rebuild_local_feed(slug, podcast=podcast)


def _parse_int_field(source, name, default=None):
    """Parse an integer from a form/dict-like source. Returns (value, error).

    Unlike Flask's ``request.form.get(name, type=int)``, an unparsable value
    is a 400 rather than silently falling back to ``default`` -- a typo'd
    season/episode number must not be swallowed.
    """
    raw = source.get(name)
    if raw is None or raw == '':
        return default, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, f'{name} must be an integer'


def _parse_published_at(value):
    """Validate + normalize an ISO 8601 (tz-aware) publishedAt string.

    Returns (normalized_iso_or_None, error). ``None`` input is valid (field
    omitted / clearing not applicable here) and passes through as None.
    """
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        return None, 'publishedAt must be an ISO 8601 datetime string'
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None, 'publishedAt must be a valid ISO 8601 datetime'
    if parsed.tzinfo is None:
        return None, 'publishedAt must include a timezone'
    return parsed.astimezone(timezone.utc).strftime(ISO_FORMAT), None


def _next_episode_number(db, podcast_id, season_number):
    """1 + the highest existing episode_number in this season (0 if none)."""
    conn = db.get_connection()
    cursor = conn.execute(
        "SELECT MAX(episode_number) FROM episodes WHERE podcast_id = ? AND season_number = ?",
        (podcast_id, season_number)
    )
    row = cursor.fetchone()
    max_ep = row[0] if row else None
    return (max_ep or 0) + 1


def _validate_p20_item(value):
    """Validate a client-supplied episode-level p20 object (person/location
    only). Returns (cleaned_dict_or_None, error). Reuses feeds.py's
    per-item validator and local_feed_builder's attr whitelists rather than
    duplicating either."""
    if not isinstance(value, dict):
        return None, 'p20 must be an object'
    tag_attrs = _p20_tag_attrs()
    cleaned = {}
    for key, val in value.items():
        if key not in _ITEM_P20_TAGS:
            return None, (f"p20: unknown tag '{key}' "
                          f"(episode-level supports: {', '.join(_ITEM_P20_TAGS)})")
        cleaned_items, err = _validate_p20_items(key, val, tag_attrs[key])
        if err:
            return None, err
        cleaned[key] = cleaned_items
    return cleaned, None


def _build_episode_updates(data):
    """Validate the shared PATCH fields (single + bulk). Returns
    (db_kwargs, error)."""
    updates = {}
    if 'title' in data:
        val = data['title']
        if val is not None and not isinstance(val, str):
            return None, 'title must be a string'
        updates['title'] = val
    if 'description' in data:
        val = data['description']
        if val is not None and not isinstance(val, str):
            return None, 'description must be a string'
        updates['description'] = val
    if 'season' in data:
        val = data['season']
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            return None, 'season must be a positive integer'
        updates['season_number'] = val
    if 'episode' in data:
        val = data['episode']
        if not isinstance(val, int) or isinstance(val, bool) or val < 1:
            return None, 'episode must be a positive integer'
        updates['episode_number'] = val
    if 'publishedAt' in data:
        normalized, err = _parse_published_at(data['publishedAt'])
        if err:
            return None, err
        updates['published_at'] = normalized
    if 'p20' in data:
        if data['p20'] is None:
            updates['p20_item_json'] = None
        else:
            cleaned, err = _validate_p20_item(data['p20'])
            if err:
                return None, err
            updates['p20_item_json'] = json.dumps(cleaned)
    return updates, None


# ========== POST /feeds/<slug>/episodes (single audio upload) ==========

@api.route('/feeds/<slug>/episodes', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def upload_local_episode(slug):
    """Upload a single episode's audio into a local feed.

    Derives episode_id = s{season:02d}e{episode:02d}; season defaults to 1,
    episode defaults to 1 + the highest existing episode_number in that
    season. Never overwrites an existing id (409). Sidecar/embedded artwork
    extraction arrives via the import path (Task 10) -- when no artwork
    field is supplied here, episode artwork is simply left absent.
    """
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    upload = request.files.get('audio')
    if upload is None or not upload.filename:
        return error_response('an audio file is required (multipart field "audio")', 400)
    if not upload.filename.lower().endswith('.mp3'):
        return error_response('only .mp3 audio is supported', 400)

    season, serr = _parse_int_field(request.form, 'season', default=1)
    if serr:
        return error_response(serr, 400)
    if season < 1:
        return error_response('season must be a positive integer', 400)

    episode_param, eerr = _parse_int_field(request.form, 'episode', default=None)
    if eerr:
        return error_response(eerr, 400)
    if episode_param is not None and episode_param < 1:
        return error_response('episode must be a positive integer', 400)

    title = request.form.get('title') or None
    description = request.form.get('description') or None

    published_at, perr = _parse_published_at(request.form.get('publishedAt') or None)
    if perr:
        return error_response(perr, 400)

    artwork_bytes = None
    artwork_content_type = None
    artwork_upload = request.files.get('artwork')
    if artwork_upload is not None and artwork_upload.filename:
        raw = artwork_upload.stream.read()
        artwork_content_type = _detect_image_mime(raw)
        if artwork_content_type not in ('image/jpeg', 'image/png'):
            return error_response('artwork must be a JPEG or PNG image', 400)
        artwork_bytes = raw

    episode_number = (episode_param if episode_param is not None
                      else _next_episode_number(db, podcast['id'], season))
    episode_id = f's{season:02d}e{episode_number:02d}'
    if not is_valid_episode_id(episode_id):
        return error_response('season/episode out of range', 400)

    if db.get_episode(slug, episode_id):
        return error_response(f'Episode {episode_id} already exists', 409)

    _, total_before = db.get_episodes(slug, status='all', limit=1)

    tmp_fd, tmp_name = tempfile.mkstemp(suffix='.mp3')
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        upload.save(tmp_path)

        duration = get_audio_duration(str(tmp_path))
        if duration is None:
            return error_response('not playable audio', 400)

        # get_original_path -> get_podcast_dir already creates the
        # episodes/ subdirectory, so no separate mkdir is needed here.
        final_path = storage.get_original_path(slug, episode_id)
        shutil.move(str(tmp_path), str(final_path))
        tmp_path = None  # ownership transferred; nothing left to clean up

        chapters = probe_chapters(str(final_path))
        if chapters and len(chapters) >= MIN_PRESERVED_CHAPTERS:
            storage.save_chapters_json(slug, episode_id, {
                'version': '1.2.0',
                'chapters': [
                    {'startTime': int(ch['start']), 'title': ch.get('title') or ''}
                    for ch in chapters
                ],
            })

        if artwork_bytes:
            storage.save_episode_artwork(slug, episode_id, artwork_bytes, artwork_content_type)

        published_at = published_at or utc_now_iso()

        db.upsert_episode(
            slug, episode_id,
            title=title,
            description=description,
            status=EpisodeStatus.DISCOVERED.value,
            original_url=f'local://{episode_id}',
            published_at=published_at,
            episode_number=episode_number,
            season_number=season,
            original_duration=duration,
        )

        queued = False
        if total_before >= 1 and db.is_auto_process_enabled_for_podcast(slug, podcast=podcast):
            priority = compute_queue_priority(
                podcast.get('queue_priority'), published_at, manual=False,
                apply_fresh_boost=db.get_setting_bool('process_new_episodes_first', True))
            queue_id = db.queue_episode_for_processing(
                slug, episode_id, f'local://{episode_id}', title, published_at,
                description, priority=priority)
            queued = queue_id is not None

        _rebuild(slug, podcast=podcast)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()

    episode = db.get_episode(slug, episode_id)
    response = _episode_base_json(episode)
    response['episodeNumber'] = episode.get('episode_number')
    response['seasonNumber'] = episode.get('season_number')
    response['queued'] = queued
    return json_response(response, 201)


# ========== PATCH /feeds/<slug>/episodes/<episode_id> (single edit) ==========

@api.route('/feeds/<slug>/episodes/<episode_id>', methods=['PATCH'])
@log_request
def patch_local_episode(slug, episode_id):
    """Edit a local episode's title/description/season/episode/publishedAt/p20.

    ``season``/``episode`` only change season_number/episode_number -- the
    episode_id (already minted from the original season/episode at upload
    time) is never renamed.
    """
    db = get_database()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('Request body must be an object', 400)

    updates, err = _build_episode_updates(data)
    if err:
        return error_response(err, 400)

    if updates:
        db.upsert_episode(slug, episode_id, **updates)
    _rebuild(slug, podcast=podcast)

    updated = db.get_episode(slug, episode_id)
    response = _episode_base_json(updated)
    response['episodeNumber'] = updated.get('episode_number')
    response['seasonNumber'] = updated.get('season_number')
    return json_response(response, 200)


# ========== PATCH /feeds/<slug>/episodes (bulk edit) ==========

@api.route('/feeds/<slug>/episodes', methods=['PATCH'])
@limiter.limit("10 per minute")
@log_request
def bulk_patch_local_episodes(slug):
    """Bulk-edit local episodes. Validates every entry before applying any
    (atomic): one invalid entry -> 400, zero rows changed."""
    db = get_database()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return error_response('Request body must be a list of episode edits', 400)
    if not data:
        return error_response('Request body must not be empty', 400)
    if len(data) > MAX_BULK_EPISODES:
        return error_response(f'Maximum {MAX_BULK_EPISODES} episodes per request', 400)

    planned = []
    seen_ids = set()
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            return error_response(f'entry {i} must be an object', 400)
        episode_id = entry.get('episodeId')
        if not isinstance(episode_id, str) or not is_valid_episode_id(episode_id):
            return error_response(f'entry {i}: invalid or missing episodeId', 400)
        if episode_id in seen_ids:
            return error_response(f'entry {i}: duplicate episodeId {episode_id}', 400)
        seen_ids.add(episode_id)
        if not db.get_episode(slug, episode_id):
            return error_response(f'entry {i}: episode {episode_id} not found', 404)
        updates, ferr = _build_episode_updates(entry)
        if ferr:
            return error_response(f'entry {i} ({episode_id}): {ferr}', 400)
        planned.append((episode_id, updates))

    with db.transaction():
        for episode_id, updates in planned:
            if updates:
                db.upsert_episode(slug, episode_id, **updates)

    _rebuild(slug, podcast=podcast)
    return json_response({'updated': len(planned)}, 200)


# ========== DELETE /feeds/<slug>/episodes/<episode_id> ==========

@api.route('/feeds/<slug>/episodes/<episode_id>', methods=['DELETE'])
@log_request
def delete_local_episode(slug, episode_id):
    """Hard-delete a local episode: files + row (see delete_episode_rows).

    Unlike the subscribed-feed episode delete (which resets to 'discovered'
    for the next refresh to rediscover), a local feed has no upstream to
    rediscover from, so the row is dropped entirely.
    """
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    deleted = db.delete_episode_rows(slug, [episode_id], storage)
    _rebuild(slug, podcast=podcast)
    return json_response({'deleted': deleted, 'episodeId': episode_id}, 200)


# ========== POST /feeds/<slug>/episodes/<episode_id>/artwork ==========

@api.route('/feeds/<slug>/episodes/<episode_id>/artwork', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def upload_local_episode_artwork(slug, episode_id):
    """Upload cover art for a single local episode."""
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)

    upload = request.files.get('file')
    if upload is None:
        return error_response('an image file is required (multipart field "file")', 400)

    raw = upload.stream.read()
    if not raw:
        return error_response('empty file', 400)

    content_type = _detect_image_mime(raw)
    if content_type not in ('image/jpeg', 'image/png'):
        return error_response('artwork must be a JPEG or PNG image', 400)

    if not storage.save_episode_artwork(slug, episode_id, raw, content_type):
        return error_response('Failed to save artwork', 500)

    _rebuild(slug, podcast=podcast)
    return json_response({'message': 'Artwork uploaded', 'episodeId': episode_id}, 200)
