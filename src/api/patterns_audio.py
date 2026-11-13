"""Audio fingerprint routes of a pattern: source resolution and playback."""
import io
import logging
import math
import os
import tempfile
import threading

from flask import abort, request, send_file

from api import (
    api, log_request, json_response, error_response, coded_error_response,
    get_database, get_storage, retry_after_response,
)
from api import preview_cache
from api.patterns import PATTERN_ROUTE, fingerprint_response_fields
from pattern_audio import resolve_retained_original, store_pattern_audio
from audio_fingerprinter import (
    clamped_fingerprint_setting, decode_fingerprint_ints, fingerprint_is_matchable,
)
from config import FFMPEG_SHORT_TIMEOUT, is_keep_like, is_pending_review
from storage import PathContainmentError
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from utils.markers import parse_ad_markers
from utils.constants import EpisodeStatus
from utils.subprocess_registry import tracked_run

logger = logging.getLogger('podcast.api')

MAX_PREVIEW_SECONDS = 600
RETAINED_MATCH_CANDIDATES = 20
ADD_SOURCE_EPISODES = 5
MATCHES_PER_PAGE_DEFAULT = 10
MATCHES_PER_PAGE_MAX = 50
MAX_REBUILD_SECONDS = 600
MATCH_ID_MAX_DIGITS = 18
PREVIEW_ENCODERS = 2
PREVIEW_RETRY_AFTER_S = 2

_preview_slots = threading.BoundedSemaphore(PREVIEW_ENCODERS)


def no_audio_response():
    """404 for a pattern that has no fingerprint."""
    return coded_error_response('Pattern has no audio', 404, 'no_audio')


def original_not_retained_response():
    """404 for a pattern whose source episodes no longer keep their original."""
    return coded_error_response('Original audio not retained for this pattern', 404,
                                'original_not_retained')


def _usable_span(start, end):
    return (isinstance(start, (int, float)) and isinstance(end, (int, float))
            and math.isfinite(start) and math.isfinite(end) and end > start)


def resolve_audio_source(db, storage, pattern_id, match_id=None, fingerprint=None, in_scope_only=False):
    """Pick the playable source of a pattern's audio: (source, None) or (None, error response).

    fingerprint is the pattern's stored row when the caller already has it. in_scope_only keeps
    out-of-scope matches out, for a source the fingerprint may be rebuilt from.
    """
    fingerprint = fingerprint or db.get_audio_fingerprint(pattern_id, with_blob=False)
    if fingerprint is None:
        return None, no_audio_response()
    if match_id is not None:
        match = db.get_fingerprint_match(match_id)
        if match is None or match['pattern_id'] != pattern_id:
            return None, error_response('Match not found', 404)
        candidates = [match]
    else:
        candidates = [
            {'podcast_slug': m['podcast_slug'], 'episode_id': m['episode_id'],
             'match_start': m['match_start'], 'match_end': m['match_end']}
            for m in db.get_retained_fingerprint_matches(
                pattern_id, RETAINED_MATCH_CANDIDATES, in_scope_only)]
        candidates.append({
            'podcast_slug': fingerprint['source_podcast_slug'],
            'episode_id': fingerprint['source_episode_id'],
            'match_start': fingerprint['source_start'], 'match_end': fingerprint['source_end']})
    for cand in candidates:
        if not cand['podcast_slug'] or not cand['episode_id'] or \
                not _usable_span(cand['match_start'], cand['match_end']):
            continue
        path, err = resolve_retained_original(db, storage, cand['podcast_slug'], cand['episode_id'])
        if err is None:
            return {'podcastSlug': cand['podcast_slug'], 'episodeId': cand['episode_id'],
                    'start': cand['match_start'], 'end': cand['match_end'], 'path': path}, None
    return None, original_not_retained_response()


def _span(source):
    return {'podcastSlug': source['podcastSlug'], 'episodeId': source['episodeId'],
            'start': source['start'], 'end': source['end']}


def _still_matchable(db, fingerprint):
    """Whether the stored fingerprint can match under the current minimum coverage."""
    ints = decode_fingerprint_ints(fingerprint['fingerprint'])
    return ints is not None and fingerprint_is_matchable(
        ints, min_coverage=clamped_fingerprint_setting(db, 'fingerprint_min_coverage'))


def _displayed_span(marker):
    """The span the episode page shows for a marker: the pre-trim range of a reviewer-adjusted one."""
    start, end = marker.get('start'), marker.get('end')
    if marker.get('reviewer_verdict') == 'adjust':
        start, end = marker.get('reviewer_original_start', start), marker.get('reviewer_original_end', end)
    # A null reviewer-original bound makes the span unusable, so the marker is skipped
    return (start, end) if _usable_span(start, end) else None


def _is_rejected_marker(marker, episode_status):
    """Whether the episode page files the marker under rejected: REJECT or never cut, and neither held nor kept."""
    if is_pending_review(marker) or is_keep_like(marker.get('action_applied')):
        return False
    was_cut = marker.get('was_cut', episode_status == EpisodeStatus.PROCESSED)
    return (marker.get('validation') or {}).get('decision') == 'REJECT' or not was_cut


def _own_marker_span(db, slug, episode_id, pattern_id):
    """The earliest displayed span of the pattern's own accepted marker in an episode, or None."""
    episode = db.get_episode(slug, episode_id)
    status = EpisodeStatus.from_api((episode or {}).get('status'))
    spans = sorted(span for marker in parse_ad_markers((episode or {}).get('ad_markers_json')) or []
                   if isinstance(marker, dict) and marker.get('pattern_id') == pattern_id
                   and not _is_rejected_marker(marker, status) and (span := _displayed_span(marker)))
    return spans[0] if spans else None


def _add_source(db, storage, pattern_id):
    """The span of the pattern's own marker that a fingerprint could be built from, or None.

    The origin episode is tried first, then up to ADD_SOURCE_EPISODES others that keep their original.
    """
    pattern = db.get_ad_pattern_by_id(pattern_id)
    origin = (pattern.get('podcast_id'), pattern.get('created_from_episode_id'))
    candidates = [origin] if all(origin) else []
    candidates += [c for c in db.get_pattern_correction_episodes(pattern_id, ADD_SOURCE_EPISODES)
                   if c != origin]
    expired = None
    for slug, episode_id in candidates:
        span = _own_marker_span(db, slug, episode_id, pattern_id)
        if span is None:
            continue
        _, err = resolve_retained_original(db, storage, slug, episode_id)
        found = {'podcastSlug': slug, 'episodeId': episode_id,
                 'start': span[0], 'end': span[1], 'available': err is None}
        if err is None:
            return found
        expired = expired or found
    return expired


def build_audio_detail(db, storage, pattern_id):
    """The audio detail object of a pattern that exists."""
    fingerprint = db.get_audio_fingerprint(pattern_id)
    summary = db.get_audio_match_summary(pattern_id)
    detail = {
        'state': 'none', 'available': False, 'matchable': True, 'length': None, 'createdAt': None,
        'builtFrom': None, 'source': None, 'editSource': None, 'addSource': None,
        'counts': {'matches': summary['match_count'], 'rejected': summary['rejected_count'],
                   'outOfScope': summary['out_of_scope_count']},
        'meanSimilarity': summary['mean_similarity'], 'lastMatchedAt': summary['last_matched_at'],
        'historyBackfillPending': db.history_backfill_pending(),
    }
    if fingerprint is None:
        detail['addSource'] = _add_source(db, storage, pattern_id)
        return detail
    detail['state'] = 'active' if fingerprint['enabled'] else 'off'
    detail['matchable'] = _still_matchable(db, fingerprint)
    detail['length'] = fingerprint['duration']
    detail['createdAt'] = fingerprint['created_at']
    slug, episode_id = fingerprint['source_podcast_slug'], fingerprint['source_episode_id']
    if slug and episode_id and _usable_span(fingerprint['source_start'], fingerprint['source_end']):
        _, built_err = resolve_retained_original(db, storage, slug, episode_id)
        detail['builtFrom'] = {'podcastSlug': slug, 'episodeId': episode_id,
                               'start': fingerprint['source_start'], 'end': fingerprint['source_end'],
                               'available': built_err is None}
    source, _ = resolve_audio_source(db, storage, pattern_id, fingerprint=fingerprint)
    if source:
        detail['source'] = _span(source)
        detail['available'] = True
    scoped, _ = resolve_audio_source(db, storage, pattern_id, fingerprint=fingerprint, in_scope_only=True)
    if scoped:
        detail['editSource'] = _span(scoped)
    # Legacy fingerprints have no recorded origin; offer an in-scope match.
    if detail['builtFrom'] is None:
        if scoped:
            detail['builtFrom'] = {**_span(scoped), 'available': True}
    return detail


def _missing_pattern(db, pattern_id):
    """The 404 response when the pattern does not exist, else None."""
    if db.get_ad_pattern_by_id(pattern_id) is None:
        return error_response('Pattern not found', 404)
    return None


def _number(value):
    """A finite non-bool number as a float, else None (an int too large for a float included)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _match_id_arg():
    """The optional matchId query argument; digits only, else 400."""
    raw = request.args.get('matchId')
    if raw is None:
        return None
    if not (raw.isascii() and raw.isdigit()) or len(raw) > MATCH_ID_MAX_DIGITS:
        abort(400, description='matchId must be an integer')
    return int(raw)


def _slice_to_mp3(path, start, end):
    """Encode [start, end] of an audio file as mp3 bytes; the temp file never outlives the call."""
    fd, tmp_path = tempfile.mkstemp(suffix='.mp3')
    os.close(fd)
    try:
        tracked_run(
            ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-v', 'error',
             '-ss', f'{start:.3f}', '-i', str(path), '-t', f'{end - start:.3f}',
             '-vn', '-c:a', 'libmp3lame', '-q:a', '4', '-f', 'mp3', tmp_path],
            capture_output=True, timeout=FFMPEG_SHORT_TIMEOUT, check=True)
        with open(tmp_path, 'rb') as f:
            data = f.read()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if not data:
        raise RuntimeError('ffmpeg produced no audio')
    return data


def _slice_key(path, start, end):
    """Cache key that changes whenever the file or the span does."""
    stat = os.stat(path)
    return (str(path), stat.st_size, stat.st_mtime_ns, start, end)


def _encode_slice(path, start, end, pattern_id):
    """The encoded slice from the cache or a new encode; None when every encoder slot is busy."""
    key = _slice_key(path, start, end)
    data = preview_cache.cached_slice(key)
    if data is not None:
        return data
    if not _preview_slots.acquire(blocking=False):
        return None
    try:
        data = _slice_to_mp3(path, start, end)
    finally:
        _preview_slots.release()
    preview_cache.remember_slice(key, data, pattern_id)
    return data


@api.route(PATTERN_ROUTE + '/audio/preview', methods=['GET'])
@log_request
def preview_pattern_audio(pattern_id):
    """Stream the source span of a pattern's audio, or of one match, as mp3."""
    db = get_database()
    if err := _missing_pattern(db, pattern_id):
        return err
    source, err = resolve_audio_source(db, get_storage(), pattern_id, _match_id_arg())
    if err:
        return err
    end = min(source['end'], source['start'] + MAX_PREVIEW_SECONDS)
    try:
        data = _encode_slice(source['path'], source['start'], end, pattern_id)
    except Exception as e:
        logger.error(f"Audio preview failed for pattern {pattern_id}: {type(e).__name__}")
        return error_response('Could not build the audio preview', 500)
    if data is None:
        return retry_after_response('Audio previews are busy, try again shortly', PREVIEW_RETRY_AFTER_S, 503)
    response = send_file(io.BytesIO(data), mimetype='audio/mpeg', conditional=True,
                         max_age=0, etag=False)
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Cache-Control'] = 'no-store'
    return response


@api.route(PATTERN_ROUTE + '/audio', methods=['GET'])
@log_request
def get_pattern_audio(pattern_id):
    """Audio fingerprint state, source and counts of a pattern."""
    db = get_database()
    if err := _missing_pattern(db, pattern_id):
        return err
    return json_response(build_audio_detail(db, get_storage(), pattern_id))


@api.route(PATTERN_ROUTE + '/audio', methods=['PUT'])
@log_request
def set_pattern_audio_enabled(pattern_id):
    """Turn a pattern's audio matching on or off."""
    db = get_database()
    if err := _missing_pattern(db, pattern_id):
        return err
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('enabled'), bool):
        return error_response('enabled must be true or false', 400)
    if not db.set_fingerprint_enabled(pattern_id, data['enabled']):
        return no_audio_response()
    return json_response(build_audio_detail(db, get_storage(), pattern_id))


@api.route(PATTERN_ROUTE + '/audio', methods=['DELETE'])
@log_request
def delete_pattern_audio(pattern_id):
    """Delete a pattern's fingerprint and match history; automatic rows will not rebuild it."""
    db = get_database()
    if err := _missing_pattern(db, pattern_id):
        return err
    deleted = db.delete_pattern_audio(pattern_id)
    preview_cache.drop_cached_slices(pattern_id)
    if not deleted:
        return no_audio_response()
    return json_response({'deleted': True})


def _rebuild_span(data):
    """Validated (slug, episode_id, start, end) of a rebuild body, or an error message."""
    slug, episode_id = data.get('podcastSlug'), data.get('episodeId')
    if not (isinstance(slug, str) and slug and isinstance(episode_id, str) and episode_id):
        return 'podcastSlug and episodeId are required'
    start, end = _number(data.get('start')), _number(data.get('end'))
    if start is None or end is None:
        return 'start and end must be finite numbers'
    if start < 0 or end <= start:
        return 'start must be at least 0 and before end'
    if end - start > MAX_REBUILD_SECONDS:
        return f'The span can be at most {MAX_REBUILD_SECONDS} seconds'
    return slug, episode_id, start, end


@api.route(PATTERN_ROUTE + '/audio/rebuild', methods=['POST'])
@log_request
def rebuild_pattern_audio(pattern_id):
    """Rebuild a pattern's fingerprint from a span of an episode's retained original."""
    db = get_database()
    if err := _missing_pattern(db, pattern_id):
        return err
    data = request.get_json(silent=True)
    parsed = _rebuild_span(data) if isinstance(data, dict) else 'A JSON object body is required'
    if isinstance(parsed, str):
        return error_response(parsed, 400)
    slug, episode_id, start, end = parsed
    episode = db.get_episode(slug, episode_id)
    if episode is None:
        return error_response('Episode not found', 404)
    duration = episode.get('original_duration')
    if duration and end > duration:
        return error_response('The span runs past the end of the episode', 400)
    result = store_pattern_audio(db, slug, episode_id, pattern_id, start, end, force=True)
    preview_cache.drop_cached_slices(pattern_id)
    if result.stored:
        db.set_pattern_audio_removed(pattern_id, False)
    return json_response({**fingerprint_response_fields([(pattern_id, result)]),
                          **build_audio_detail(db, get_storage(), pattern_id)})


def _original_on_disk(storage, row):
    """Whether the match's episode keeps its original: the column is set and the file exists."""
    if not row['episode_original_file']:
        return False
    try:
        return storage.get_original_path(row['podcast_slug'], row['episode_id']).exists()
    except PathContainmentError:
        return False


@api.route(PATTERN_ROUTE + '/audio/matches', methods=['GET'])
@log_request
def list_pattern_audio_matches(pattern_id):
    """Match history of a pattern's audio, newest first."""
    db = get_database()
    if err := _missing_pattern(db, pattern_id):
        return err
    per_page = min(max(1, request.args.get('perPage', MATCHES_PER_PAGE_DEFAULT, type=int)),
                   MATCHES_PER_PAGE_MAX)
    total = db.count_fingerprint_matches(pattern_id)
    last_page = max(1, -(-total // per_page))
    page = min(max(1, request.args.get('page', 1, type=int)), last_page + 1)
    storage = get_storage()
    fingerprint = db.get_audio_fingerprint(pattern_id, with_blob=False)
    stored_length = fingerprint['duration'] if fingerprint else None
    rows = db.get_fingerprint_matches_with_episodes(pattern_id, per_page, (page - 1) * per_page)
    matches = [{
        'id': row['id'], 'episodeId': row['episode_id'], 'podcastSlug': row['podcast_slug'],
        'episodeTitle': row['episode_title'], 'start': row['match_start'], 'end': row['match_end'],
        'similarity': row['similarity'], 'coverage': row['coverage'], 'storedLength': stored_length,
        'inScope': bool(row['in_scope']),
        'audioOnly': None if row['audio_only'] is None else bool(row['audio_only']),
        'rejected': bool(row['rejected']), 'available': _original_on_disk(storage, row),
        'createdAt': row['created_at'],
    } for row in rows]
    return json_response({'matches': matches, 'page': page, 'perPage': per_page, 'total': total})
