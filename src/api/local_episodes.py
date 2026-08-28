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
                 get_database, get_storage, get_status_service)
from api.episodes import _episode_base_json
from api.feeds import _validate_p20_items, _p20_tag_attrs
from config import MIN_PRESERVED_CHAPTERS
from database.podcasts import is_local_feed
from database.queue import compute_queue_priority
from embedded_chapters import probe_chapters
from local_import import build_import_plan, get_import_status, plan_hash, start_commit
from processing_queue import ProcessingQueue
from storage import _detect_image_mime
from utils.audio import extract_embedded_artwork, get_audio_duration
from utils.constants import EpisodeStatus
from utils.time import ISO_FORMAT, utc_now_iso
from utils.validation import is_dangerous_slug, is_valid_episode_id

logger = logging.getLogger('podcast.api')

MAX_BULK_EPISODES = 500

# Cap on the in-memory artwork read (upload/PATCH artwork fields). The
# single-episode-upload route's request cap is widened to 1 GB for the
# audio field (see api/__init__.py's _widen_upload_cap), so an unbounded
# .stream.read() on the co-located artwork field would let a client force
# up to that much into memory just for the image part.
MAX_EPISODE_ARTWORK_BYTES = 10 * 1024 * 1024

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


def _read_capped(file_storage, max_bytes):
    """Read at most ``max_bytes + 1`` bytes from a Werkzeug FileStorage
    stream -- never buffers more than that regardless of how large the
    underlying request is. Returns (data, too_large); when too_large is
    True, ``data`` must be discarded rather than persisted."""
    data = file_storage.stream.read(max_bytes + 1)
    return data, len(data) > max_bytes


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


# Fields a single/bulk PATCH may set. Bulk entries additionally carry
# 'episodeId' (the routing key, not an update field) -- callers pass that
# through allowed_extra_keys rather than folding it in here, so this set
# stays the same list _build_episode_updates below actually understands.
_EPISODE_PATCH_KEYS = frozenset({'title', 'description', 'season', 'episode',
                                 'publishedAt', 'p20'})


def _build_episode_updates(data, *, allowed_extra_keys=frozenset()):
    """Validate the shared PATCH fields (single + bulk). Returns
    (db_kwargs, error).

    Fail-closed on an unrecognized key -- a typo like 'publishedat' 400s
    naming the key instead of silently no-oping, matching the import
    sidecar's own fail-closed unknown-key behavior (validate_sidecar)."""
    unknown = set(data) - _EPISODE_PATCH_KEYS - allowed_extra_keys
    if unknown:
        return None, f"unknown field(s): {', '.join(sorted(unknown))}"

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
@log_request
def upload_local_episode(slug):
    """Upload a single episode's audio into a local feed.

    Derives episode_id = s{season:02d}e{episode:02d}; season defaults to 1,
    episode defaults to 1 + the highest existing episode_number in that
    season. Never overwrites an existing id (409). No title -> defaults to
    'Episode {n}', matching the import path's fallback. When no artwork
    field is supplied, embedded cover art is extracted from the audio file
    the same way the import path's _commit_entry does.

    No dedicated rate limit here (unlike the other mutating routes): a 1 GB
    audio upload already self-limits request throughput far below anything
    a per-minute counter would add.
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
        raw, too_large = _read_capped(artwork_upload, MAX_EPISODE_ARTWORK_BYTES)
        if too_large:
            return error_response(
                f'artwork must be {MAX_EPISODE_ARTWORK_BYTES // (1024 * 1024)} MB or smaller', 400)
        artwork_content_type = _detect_image_mime(raw)
        if artwork_content_type not in ('image/jpeg', 'image/png'):
            return error_response('artwork must be a JPEG or PNG image', 400)
        artwork_bytes = raw

    episode_number = (episode_param if episode_param is not None
                      else _next_episode_number(db, podcast['id'], season))
    episode_id = f's{season:02d}e{episode_number:02d}'
    if not is_valid_episode_id(episode_id):
        return error_response('season/episode out of range', 400)

    if not title:
        title = f'Episode {episode_number}'

    if db.get_episode(slug, episode_id):
        return error_response(f'Episode {episode_id} already exists', 409)

    _, total_before = db.get_episodes(slug, status='all', limit=1)

    # get_original_path -> get_podcast_dir already creates the episodes/
    # subdirectory. The tempfile is created in that SAME directory so the
    # later shutil.move is a same-filesystem rename rather than a
    # cross-device copy -- doubling disk usage and I/O time on a 1 GB
    # upload if DATA_DIR and the system tmp dir are on different mounts.
    final_path = storage.get_original_path(slug, episode_id)
    tmp_fd, tmp_name = tempfile.mkstemp(suffix='.mp3', dir=str(final_path.parent))
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    try:
        upload.save(tmp_path)

        duration = get_audio_duration(str(tmp_path))
        if duration is None:
            return error_response('not playable audio', 400)

        shutil.move(str(tmp_path), str(final_path))
        tmp_path = None  # ownership transferred; nothing left to clean up

        published_at = published_at or utc_now_iso()

        # Insert the row immediately after the move. This narrows the
        # window where the audio file is on disk with no corresponding DB
        # row, and -- more importantly -- the chapters/artwork writes
        # below need the row to already exist: save_chapters_json goes
        # through save_episode_details, which raises ValueError (silently
        # swallowed to a warning by the storage layer) when the episode
        # isn't in the episodes table yet.
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
            # The retained original IS the audio just moved into place above
            # -- without this, hasOriginalAudio stays false and the
            # /original.mp3 route 404s until a processing run happens to
            # write this column itself (main_app/processing.py).
            original_file=f'{episode_id}-original.mp3',
        )

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
            # evict=False: this is the only copy of this cover (no
            # upstream URL to re-download it from later), so it must never
            # be dropped by the episode-artwork LRU cache trim.
            storage.save_episode_artwork(slug, episode_id, artwork_bytes,
                                         artwork_content_type, evict=False)
        else:
            # No artwork field supplied: fall back to whatever cover art is
            # embedded in the audio itself, exactly like the import path's
            # _commit_entry -- otherwise an upload never gets a cover unless
            # the caller happens to also POST one separately.
            embedded = extract_embedded_artwork(str(final_path))
            if embedded:
                image_data, mime = embedded
                storage.save_episode_artwork(slug, episode_id, image_data, mime,
                                             evict=False)

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
        updates, ferr = _build_episode_updates(entry, allowed_extra_keys={'episodeId'})
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

    # A worker actively processing this episode is reading/writing its
    # files right now; deleting out from under it would race the worker
    # and leave it referencing a gone row/file.
    if ProcessingQueue().is_processing(slug, episode_id):
        return error_response('episode is processing; cancel it first', 409)

    deleted = db.delete_episode_rows(slug, [episode_id], storage)

    # delete_episode_rows already drops the auto_process_queue row; also
    # drop it from the live display queue (a separate, in-memory/JSON
    # status store) so a still-queued-but-not-yet-claimed entry doesn't
    # linger in the UI for a row that no longer exists.
    get_status_service().remove_queued_episode(slug, episode_id)

    _rebuild(slug, podcast=podcast)
    return json_response({'deleted': deleted, 'episodeId': episode_id}, 200)


# ========== POST /feeds/<slug>/episodes/<episode_id>/artwork ==========

@api.route('/feeds/<slug>/episodes/<episode_id>/artwork', methods=['POST'])
@limiter.limit("60 per minute")
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

    raw, too_large = _read_capped(upload, MAX_EPISODE_ARTWORK_BYTES)
    if too_large:
        return error_response(
            f'artwork must be {MAX_EPISODE_ARTWORK_BYTES // (1024 * 1024)} MB or smaller', 400)
    if not raw:
        return error_response('empty file', 400)

    content_type = _detect_image_mime(raw)
    if content_type not in ('image/jpeg', 'image/png'):
        return error_response('artwork must be a JPEG or PNG image', 400)

    # evict=False: the only copy of this cover -- see upload_local_episode's
    # identical note.
    if not storage.save_episode_artwork(slug, episode_id, raw, content_type, evict=False):
        return error_response('Failed to save artwork', 500)

    _rebuild(slug, podcast=podcast)
    return json_response({'message': 'Artwork uploaded', 'episodeId': episode_id}, 200)


# ========== Bulk archive import (#625 Task 11) ==========

# Plan-entry keys that resolve to absolute server-side filesystem paths.
# build_import_plan puts these in each entry for the commit engine to read
# files by exact resolved path (local_import.py:381-392) -- they must never
# reach the client, which only needs the basenames (audioFile etc, kept).
_PLAN_INTERNAL_PATH_KEYS = ('audioPath', 'descriptionPath', 'artworkPath', 'sidecarPath')

_IMPORT_SOURCE_CHOICES = ('staging', 'directory', 'both')

# Most filesystems (ext4, xfs, btrfs, ...) cap a single path component at
# 255 bytes; an over-long name makes the eventual open()/rename() raise
# ENAMETOOLONG. Checked up front (in UTF-8 bytes, matching how the kernel
# counts) so it becomes an ordinary per-file rejection rather than the
# OSError bubbling out of upload.save() below and 500ing the whole batch.
MAX_IMPORT_BASENAME_BYTES = 255


def _reject_reason_for_basename(name):
    """None if ``name`` is safe to use as a staged file's basename, else a
    human-readable rejection reason. Mirrors is_dangerous_slug's traversal
    checks (.., /, \\, NUL) plus a dotfile/empty-name/length check --
    filenames are far more permissive than slugs (spaces, case,
    punctuation are all fine), so this doesn't reuse is_valid_slug's
    strict charset."""
    if not name:
        return 'empty filename'
    if is_dangerous_slug(name):
        return 'invalid filename'
    if name.startswith('.'):
        return 'hidden file (dotfile)'
    if len(name.encode('utf-8')) > MAX_IMPORT_BASENAME_BYTES:
        return f'filename too long (max {MAX_IMPORT_BASENAME_BYTES} bytes)'
    return None


def _collect_import_sources(storage, slug, source):
    """Files (not directories) under the requested source dir(s), sorted by
    name for a deterministic plan/hash. Neither staging nor the
    user-managed import dir is guaranteed to exist -- an absent directory
    just contributes no files."""
    dirs = []
    if source in ('staging', 'both'):
        dirs.append(storage.import_staging_dir(slug, create=False))
    if source in ('directory', 'both'):
        dirs.append(storage.import_source_dir(slug))

    sources = []
    for d in dirs:
        if not d.is_dir():
            continue
        sources.extend(p for p in sorted(d.iterdir()) if p.is_file())
    return sources


def _client_import_plan(plan):
    """The scan/commit-mismatch response payload: ``plan`` with every
    entry's internal absolute-path keys stripped (Task 10 review contract
    -- server filesystem layout must never leave the server). mtimeNs and
    everything else pass through verbatim."""
    entries = [
        {k: v for k, v in entry.items() if k not in _PLAN_INTERNAL_PATH_KEYS}
        for entry in plan['entries']
    ]
    return {**plan, 'entries': entries}


def _existing_episode_ids(db, slug):
    episodes, _ = db.get_episodes(slug, status='all', limit=10000)
    return {ep['episode_id'] for ep in episodes}


def _parse_import_request(data):
    """Shared body validation for scan/commit. Returns
    (source, overwrite, error_response_or_None)."""
    if not isinstance(data, dict):
        return None, None, error_response('Request body must be an object', 400)
    source = data.get('source', 'both')
    if source not in _IMPORT_SOURCE_CHOICES:
        return None, None, error_response(
            f'source must be one of {", ".join(_IMPORT_SOURCE_CHOICES)}', 400)
    overwrite = data.get('overwrite', False)
    if not isinstance(overwrite, bool):
        return None, None, error_response('overwrite must be a boolean', 400)
    return source, overwrite, None


# ---------- POST /feeds/<slug>/import/upload ----------

@api.route('/feeds/<slug>/import/upload', methods=['POST'])
@limiter.exempt
@log_request
def upload_import_files(slug):
    """Stage one or more archive-import files ahead of scan/commit.

    Multipart, repeated field ``files``; each is saved under its ORIGINAL
    basename into import_staging_dir(slug, create=True) -- the sNNeNN
    naming scheme (parsed later by build_import_plan) needs the real
    filename, not a generated one. A basename containing a path separator,
    '..', NUL, or that's empty/a dotfile is rejected per-file rather than
    failing the whole request. Covered by _widen_upload_cap's 1 GB cap
    (api/__init__.py); no dedicated per-route rate limit for the same
    reason as upload_local_episode -- a large multipart upload already
    self-limits. Exempted from the blueprint's default per-IP limits too:
    the UI uploads a batch one file per request (needed for per-file
    progress), and a batch past ~200 files would otherwise start 429ing
    partway through on the default 200/min cap.
    """
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    uploads = request.files.getlist('files')
    if not uploads:
        return error_response('at least one file is required (multipart field "files")', 400)

    staged = []
    rejected = []
    staging_dir = None
    for upload in uploads:
        name = upload.filename or ''
        reason = _reject_reason_for_basename(name)
        if reason:
            rejected.append({'file': name, 'reason': reason})
            continue
        if staging_dir is None:
            staging_dir = storage.import_staging_dir(slug, create=True)
        try:
            upload.save(str(staging_dir / name))
        except OSError as exc:
            # Belt-and-suspenders alongside the length check above: any
            # other filesystem-rejected name (e.g. reserved characters on
            # a non-POSIX mount) still degrades to a per-file rejection
            # rather than a 500 for the whole batch.
            rejected.append({'file': name, 'reason': f'could not save file: {exc}'})
            continue
        staged.append(name)

    return json_response({'staged': staged, 'rejected': rejected}, 200)


# ---------- POST /feeds/<slug>/import/scan ----------

@api.route('/feeds/<slug>/import/scan', methods=['POST'])
@log_request
def scan_import(slug):
    """Dry-run preview: scans staging/the import dir and returns the plan
    the UI renders for review before commit."""
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    data = request.get_json(silent=True)
    if data is None:
        data = {}
    source, overwrite, verr = _parse_import_request(data)
    if verr:
        return verr

    sources = _collect_import_sources(storage, slug, source)
    existing_ids = _existing_episode_ids(db, slug)
    plan = build_import_plan(slug, sources, existing_ids,
                             overwrite=overwrite, now_iso=utc_now_iso())
    return json_response(_client_import_plan(plan), 200)


# ---------- POST /feeds/<slug>/import/commit ----------

@api.route('/feeds/<slug>/import/commit', methods=['POST'])
@log_request
def commit_import(slug):
    """Start committing a previously scanned plan.

    Re-scans server-side and compares the client-echoed planHash rather
    than trusting anything the client sends beyond planHash/source/
    overwrite -- a 409 on mismatch means either the files changed
    underneath the scan, or this commit's overwrite doesn't match what the
    reviewed plan was scanned with (plan_hash folds overwrite into the
    hash for exactly this; see its docstring). The freshly rebuilt
    server-side plan (with paths) is what's handed to start_commit, never
    client-supplied entries.
    """
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('Request body must be an object', 400)

    client_hash = data.get('planHash')
    if not isinstance(client_hash, str) or not client_hash:
        return error_response('planHash is required', 400)

    source, overwrite, verr = _parse_import_request(data)
    if verr:
        return verr

    sources = _collect_import_sources(storage, slug, source)
    existing_ids = _existing_episode_ids(db, slug)
    plan = build_import_plan(slug, sources, existing_ids,
                             overwrite=overwrite, now_iso=utc_now_iso())
    if plan['planHash'] != client_hash:
        # Disambiguate the two ways a hash can go stale: the flag folds
        # into plan_hash (see its docstring), so a client-echoed hash that
        # matches what THIS scan would have produced with the opposite
        # overwrite value means nothing on disk changed -- the checkbox
        # was just flipped after the scan -- and the operator needs a
        # different fix (re-scan, not re-check files) than a real content
        # change would call for.
        if plan_hash(sources, not overwrite) == client_hash:
            return error_response(
                'overwrite setting changed since scan; re-run scan', 409)
        return error_response('files changed since scan; re-run scan', 409)

    if plan.get('batchErrors'):
        return error_response(
            '; '.join(plan['batchErrors']), 400,
            details={'batchErrors': plan['batchErrors']})

    started, message = start_commit(slug, plan, db=db, storage=storage)
    if not started:
        return error_response(message, 409)
    return json_response({'message': 'import started'}, 202)


# ---------- GET /feeds/<slug>/import/status ----------

@api.route('/feeds/<slug>/import/status', methods=['GET'])
@log_request
def import_status(slug):
    """Passthrough of local_import.get_import_status for the UI's poll."""
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    return json_response(get_import_status(slug, storage), 200)


# ---------- DELETE /feeds/<slug>/import/staging ----------

@api.route('/feeds/<slug>/import/staging', methods=['DELETE'])
@log_request
def clear_import_staging(slug):
    """Empty the feed's upload staging directory.

    Staging accumulates across canceled/abandoned import attempts: every
    upload lands there and only gets cleared by a successful commit, so a
    scan after a few canceled tries returns the union of every file ever
    staged, not just the operator's latest batch. This gives the UI an
    explicit way to wipe it clean -- called from the plan-clearing Cancel
    button and from the "Clear staged files" affordance that appears when
    a scan's plan includes more than what was just uploaded.

    409s while an import is running for this feed: the commit engine reads
    staged files by their resolved path (build_import_plan's audioPath/
    etc.), so wiping the directory out from under a live commit would turn
    in-flight entries into 'source file no longer present' failures
    instead of leaving the running import alone.

    Removed per-entry rather than via a single rmtree of the whole
    directory: a busy bind-mount point (e.g. staging mounted from a
    network share) can refuse to remove ITSELF while still allowing its
    contents to go one at a time, so a single stubborn entry must not
    block every other file from being cleared.
    """
    db = get_database()
    storage = get_storage()

    podcast, err = _require_local_feed(db, slug)
    if err:
        return err

    if get_import_status(slug, storage).get('state') == 'running':
        return error_response('cannot clear staging while an import is running', 409)

    staging_dir = storage.import_staging_dir(slug, create=False)
    if staging_dir.is_dir():
        for child in staging_dir.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink()
            except OSError:
                logger.warning(f"[{slug}] could not remove staged file {child}")

    return json_response({'message': 'staging cleared'}, 200)
