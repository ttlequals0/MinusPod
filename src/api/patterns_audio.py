"""Audio fingerprint routes of a pattern: source resolution and playback."""
import io
import logging
import math
import os
import tempfile

from flask import abort, request, send_file

from api import (
    api, log_request, json_response, error_response, coded_error_response,
    get_database, get_storage, _resolve_original_audio,
)
from api.patterns import _store_pattern_audio
from config import FFMPEG_SHORT_TIMEOUT
from storage import PathContainmentError
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from utils.subprocess_registry import tracked_run

logger = logging.getLogger('podcast.api')

MAX_PREVIEW_SECONDS = 600
RETAINED_MATCH_CANDIDATES = 20
MATCHES_PER_PAGE_DEFAULT = 10
MATCHES_PER_PAGE_MAX = 50


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


def resolve_audio_source(db, storage, pattern_id, match_id=None):
    """Pick the playable source span of a pattern's audio.

    Returns (source, None) with source {podcastSlug, episodeId, start, end, path}, or
    (None, error response). Order: the given match, else the newest non-rejected match
    whose original is retained, else the episode the fingerprint was built from.
    """
    fingerprint = db.get_audio_fingerprint(pattern_id)
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
            for m in db.get_retained_fingerprint_matches(pattern_id, RETAINED_MATCH_CANDIDATES)]
        candidates.append({
            'podcast_slug': fingerprint['source_podcast_slug'],
            'episode_id': fingerprint['source_episode_id'],
            'match_start': fingerprint['source_start'], 'match_end': fingerprint['source_end']})
    for cand in candidates:
        if not cand['podcast_slug'] or not cand['episode_id'] or \
                not _usable_span(cand['match_start'], cand['match_end']):
            continue
        path, err = _resolve_original_audio(db, storage, cand['podcast_slug'], cand['episode_id'])
        if err is None:
            return {'podcastSlug': cand['podcast_slug'], 'episodeId': cand['episode_id'],
                    'start': cand['match_start'], 'end': cand['match_end'], 'path': path}, None
    return None, original_not_retained_response()


def _span(source):
    return {'podcastSlug': source['podcastSlug'], 'episodeId': source['episodeId'],
            'start': source['start'], 'end': source['end']}


def build_audio_detail(db, storage, pattern_id):
    """The audio detail object of a pattern that exists."""
    fingerprint = db.get_audio_fingerprint(pattern_id)
    summary = db.get_audio_match_summary(pattern_id)
    detail = {
        'state': 'none', 'available': False, 'length': None, 'createdAt': None,
        'builtFrom': None, 'source': None,
        'counts': {'matches': summary['match_count'], 'rejected': summary['rejected_count'],
                   'outOfScope': summary['out_of_scope_count']},
        'meanSimilarity': summary['mean_similarity'], 'lastMatchedAt': summary['last_matched_at'],
    }
    if fingerprint is None:
        return detail
    detail['state'] = 'active' if fingerprint['enabled'] else 'off'
    detail['length'] = fingerprint['duration']
    detail['createdAt'] = fingerprint['created_at']
    slug, episode_id = fingerprint['source_podcast_slug'], fingerprint['source_episode_id']
    if slug and episode_id and _usable_span(fingerprint['source_start'], fingerprint['source_end']):
        _, built_err = _resolve_original_audio(db, storage, slug, episode_id)
        detail['builtFrom'] = {'podcastSlug': slug, 'episodeId': episode_id,
                               'start': fingerprint['source_start'], 'end': fingerprint['source_end'],
                               'available': built_err is None}
    source, _ = resolve_audio_source(db, storage, pattern_id)
    if source:
        detail['source'] = _span(source)
        detail['available'] = True
    return detail


def _known_pattern(db, pattern_id):
    """The pattern row, or (None, 404 response)."""
    pattern = db.get_ad_pattern_by_id(pattern_id)
    if pattern is None:
        return None, error_response('Pattern not found', 404)
    return pattern, None


def _number(value):
    """A finite non-bool number as a float, else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _match_id_arg():
    """The optional matchId query argument; digits only, else 400."""
    raw = request.args.get('matchId')
    if raw is None:
        return None
    if not (raw.isascii() and raw.isdigit()):
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


@api.route('/patterns/<int:pattern_id>/audio/preview', methods=['GET'])
@log_request
def preview_pattern_audio(pattern_id):
    """Stream the source span of a pattern's audio, or of one match, as mp3."""
    db = get_database()
    if not db.get_ad_pattern_by_id(pattern_id):
        return error_response('Pattern not found', 404)
    source, err = resolve_audio_source(db, get_storage(), pattern_id, _match_id_arg())
    if err:
        return err
    end = min(source['end'], source['start'] + MAX_PREVIEW_SECONDS)
    try:
        data = _slice_to_mp3(source['path'], source['start'], end)
    except Exception as e:
        logger.error(f"Audio preview failed for pattern {pattern_id}: {type(e).__name__}")
        return error_response('Could not build the audio preview', 500)
    response = send_file(io.BytesIO(data), mimetype='audio/mpeg', conditional=True,
                         max_age=0, etag=False)
    response.headers['Accept-Ranges'] = 'bytes'
    response.headers['Cache-Control'] = 'no-store'
    return response


@api.route('/patterns/<int:pattern_id>/audio', methods=['GET'])
@log_request
def get_pattern_audio(pattern_id):
    """Audio fingerprint state, source and counts of a pattern."""
    db = get_database()
    _, err = _known_pattern(db, pattern_id)
    if err:
        return err
    return json_response(build_audio_detail(db, get_storage(), pattern_id))


@api.route('/patterns/<int:pattern_id>/audio', methods=['PUT'])
@log_request
def set_pattern_audio_enabled(pattern_id):
    """Turn a pattern's audio matching on or off."""
    db = get_database()
    _, err = _known_pattern(db, pattern_id)
    if err:
        return err
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get('enabled'), bool):
        return error_response('enabled must be true or false', 400)
    if not db.set_fingerprint_enabled(pattern_id, data['enabled']):
        return no_audio_response()
    return json_response(build_audio_detail(db, get_storage(), pattern_id))


@api.route('/patterns/<int:pattern_id>/audio', methods=['DELETE'])
@log_request
def delete_pattern_audio(pattern_id):
    """Delete a pattern's fingerprint and match history; automatic rows will not rebuild it."""
    db = get_database()
    _, err = _known_pattern(db, pattern_id)
    if err:
        return err
    if not db.delete_pattern_audio(pattern_id):
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
    return slug, episode_id, start, end


@api.route('/patterns/<int:pattern_id>/audio/rebuild', methods=['POST'])
@log_request
def rebuild_pattern_audio(pattern_id):
    """Rebuild a pattern's fingerprint from a span of an episode's retained original."""
    db = get_database()
    _, err = _known_pattern(db, pattern_id)
    if err:
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
    result = _store_pattern_audio(db, slug, episode_id, pattern_id, start, end, force=True)
    if result.stored:
        db.set_pattern_audio_removed(pattern_id, False)
    body = {'fingerprintStored': result.stored}
    if not result.stored:
        body['fingerprintSkippedReason'] = result.skipped_reason
    body['audio'] = build_audio_detail(db, get_storage(), pattern_id)
    return json_response(body)


def _original_on_disk(storage, row):
    """Whether the match's episode keeps its original: the column is set and the file exists."""
    if not row['episode_original_file']:
        return False
    try:
        return storage.get_original_path(row['podcast_slug'], row['episode_id']).exists()
    except PathContainmentError:
        return False


@api.route('/patterns/<int:pattern_id>/audio/matches', methods=['GET'])
@log_request
def list_pattern_audio_matches(pattern_id):
    """Match history of a pattern's audio, newest first."""
    db = get_database()
    _, err = _known_pattern(db, pattern_id)
    if err:
        return err
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(max(1, request.args.get('perPage', MATCHES_PER_PAGE_DEFAULT, type=int)),
                   MATCHES_PER_PAGE_MAX)
    storage = get_storage()
    fingerprint = db.get_audio_fingerprint(pattern_id)
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
    return json_response({'matches': matches, 'page': page, 'perPage': per_page,
                          'total': db.count_fingerprint_matches(pattern_id)})
